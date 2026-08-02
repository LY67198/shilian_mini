import api from './request'

export function startInterview(data) {
  // RAG 出题链路（embedding + rerank + DeepSeek 选题）可长达 2 分钟，超时放宽到 180s
  return api.post('/interviews/start', data, { timeout: 180000 })
}

export async function startInterviewStream(data, onStatus, onQuestion, onDone, signal) {
  const authStore = (await import('../stores/auth')).useAuthStore()
  const response = await fetch('/api/v1/interviews/start?stream=true', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authStore.token}`
    },
    body: JSON.stringify(data),
    signal
  })

  // 非 200（401/5xx 等非 SSE body）→ 流静默结束会让 onDone/onError 不触发、UI 卡死，
  // 必须抛错由 handleStart 的 catch 统一兜底
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`面试启动失败 (HTTP ${response.status})${detail ? ': ' + detail : ''}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let lineBuffer = ''      // SSE 行拆分缓冲
  let jsonBuffer = ''      // JSON 增量累积缓冲（LLM 输出原文）
  let currentEvent = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      lineBuffer += decoder.decode(value, { stream: true })

      const lines = lineBuffer.split('\n')
      lineBuffer = lines.pop() || ''

      for (const line of lines) {
        if (line === '') {
          currentEvent = ''
          continue
        }
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'status':
                onStatus(data.message)
                break
              case 'chunk':
                jsonBuffer += data.content
                const parsed = tryParseQuestions(jsonBuffer)
                if (parsed && parsed.length) onQuestion(parsed)
                break
              case 'error':
                throw new Error(data.message || data.error || 'Unknown error')
              case 'done':
                onDone(data)
                break
            }
          } catch (e) {
            if (e.message !== 'Unexpected end of JSON input') throw e
          }
        }
      }
    }
  } finally {
    try { reader.releaseLock() } catch (_) {}
  }
}

function tryParseQuestions(buffer) {
  const trimmed = buffer.trim()
  if (!trimmed) return null
  try {
    const arr = JSON.parse(trimmed)
    return Array.isArray(arr) ? arr : null
  } catch (_) {}
  if (trimmed.endsWith(']')) return null
  try {
    const arr = JSON.parse(trimmed + ']')   // 补右括号提前解出已完整对象
    return Array.isArray(arr) ? arr : null
  } catch (_) {
    return null                              // 末尾对象未闭合 → 保留上次结果
  }
}

// 对象型增量 JSON 解析：能解出多少字段就返回多少（SSE 流式反馈/报告用）。
// 与 tryParseQuestions（数组，补右括号）互补：这里是对象，用字段正则提取
// 已闭合/正在闭合的字符串字段前缀，实现逐字显示。
export function tryParseStreamObject(buffer) {
  const t = buffer.trim()
  if (!t.startsWith('{')) return null
  try {
    const obj = JSON.parse(t)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj
  } catch (_) {}
  const result = {}
  const fb = t.match(/"feedback"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (fb && fb[1]) result.feedback = fb[1]
  const sum = t.match(/"summary"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (sum && sum[1]) result.summary = sum[1]
  const rec = t.match(/"hire_recommendation"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (rec && rec[1]) result.hire_recommendation = rec[1]
  const sc = t.match(/"score"\s*:\s*(\d+(?:\.\d+)?)/)
  if (sc) result.score = parseFloat(sc[1])
  return Object.keys(result).length ? result : null
}

export function submitAnswer(interviewId, answer) {
  return api.post(`/interviews/${interviewId}/answer`, { answer })
}

export async function submitAnswerStream(interviewId, answer, onChunk, onDone, signal) {
  const authStore = (await import('../stores/auth')).useAuthStore()
  const response = await fetch(`/api/v1/interviews/${interviewId}/answer?stream=true`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authStore.token}`
    },
    body: JSON.stringify({ answer }),
    signal
  })

  // 非 200（401/5xx 等非 SSE body）→ 流静默结束会让 onDone/onError 不触发、UI 卡死，
  // 必须抛错由调用方 catch 统一兜底（错误前缀区别于 start 流程）
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`提交回答失败 (HTTP ${response.status})${detail ? ': ' + detail : ''}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  let currentEvent = ''
  let scoreData = null
  let nextQuestionData = null

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        // Blank line = SSE dispatch boundary
        if (line === '') {
          currentEvent = ''
          continue
        }
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'chunk':
                onChunk(data.content)
                break
              case 'score':
                scoreData = data
                break
              case 'next_question':
                nextQuestionData = data
                break
              case 'error':
                throw new Error(data.message || data.error || 'Unknown error')
              case 'done':
                onDone({
                  score: scoreData?.score,
                  feedback: scoreData?.feedback,
                  follow_up: scoreData?.follow_up,
                  is_finished: !nextQuestionData,
                  next_question: nextQuestionData?.question,
                  question_index: nextQuestionData?.index,
                })
                break
            }
          } catch (e) {
            if (e.message !== 'Unexpected end of JSON input') throw e
          }
        }
      }
    }
  } finally {
    try { reader.releaseLock() } catch (_) {}
  }
}

export function getReport(interviewId) {
  return api.get(`/interviews/${interviewId}/report`)
}

export function getMessages(interviewId) {
  return api.get(`/interviews/${interviewId}/messages`)
}

export function getInterviews() {
  return api.get('/interviews')
}

export function deleteInterview(interviewId) {
  return api.delete(`/interviews/${interviewId}`)
}
