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

// 从累积的题目 JSON 原文提取最后一个 "question": "..." 的文本前缀（逐字增长）。
// 兼容 select_one（含 category/bank_id）与 generate_one（含 reference_answer）两种单对象输出。
export function extractQuestionText(buffer) {
  const t = (buffer || '').trim()
  if (!t) return ''
  const re = /"question"\s*:\s*"((?:\\.|[^"\\])*)/g
  let m
  let last = ''
  while ((m = re.exec(t)) !== null) {
    last = m[1]
    if (m.index === re.lastIndex) re.lastIndex++  // 防空匹配死循环
  }
  return last
}

export async function generateNextQuestion(interviewId, onStatus, onQuestionChunk, onDone, signal) {
  const authStore = (await import('../stores/auth')).useAuthStore()
  const response = await fetch(`/api/v1/interviews/${interviewId}/next-question?stream=true`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authStore.token}`
    },
    body: '{}',
    signal
  })

  // 非 200（401/5xx 等非 SSE body）→ 抛错由调用方 catch 统一兜底
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`生成题目失败 (HTTP ${response.status})${detail ? ': ' + detail : ''}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let lineBuffer = ''      // SSE 行拆分缓冲
  let jsonBuffer = ''      // 题目 JSON 原文累积缓冲
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
              case 'question_chunk':
                jsonBuffer += data.content
                const qText = extractQuestionText(jsonBuffer)
                if (qText) onQuestionChunk(qText)
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

// 对象型增量 JSON 解析：能解出多少字段就返回多少（SSE 流式反馈/报告用）。
// 与 tryParseQuestions（数组，补右括号）互补：这里是对象，用字段正则提取
// 已闭合/正在闭合的字符串字段前缀，实现逐字显示。
export function tryParseStreamObject(buffer) {
  const t = buffer.trim()
  if (!t.startsWith('{')) return null

  // 同一连接内 evaluate → generate_report 可能串流两个根对象（无分界事件），
  // 用带引号感知的括号扫描定位最后一个根对象的起点，丢弃已闭合的旧对象。
  let start = 0
  let depth = 0
  let inStr = false
  let esc = false
  for (let i = 0; i < t.length; i++) {
    const c = t[i]
    if (inStr) {
      if (esc) esc = false
      else if (c === '\\') esc = true
      else if (c === '"') inStr = false
      continue
    }
    if (c === '"') inStr = true
    else if (c === '{') { if (depth === 0) start = i; depth++ }
    else if (c === '}') depth--
  }
  const root = start > 0 ? t.slice(start) : t

  try {
    const obj = JSON.parse(root)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj
  } catch (_) {}
  const result = {}
  const fb = root.match(/"feedback"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (fb && fb[1]) result.feedback = fb[1]
  const sum = root.match(/"summary"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (sum && sum[1]) result.summary = sum[1]
  const rec = root.match(/"hire_recommendation"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (rec && rec[1]) result.hire_recommendation = rec[1]
  const sc = root.match(/"score"\s*:\s*(\d+(?:\.\d+)?)/)
  if (sc) result.score = parseFloat(sc[1])
  return Object.keys(result).length ? result : null
}

export function submitAnswer(interviewId, answer) {
  return api.post(`/interviews/${interviewId}/answer`, { answer })
}

export async function submitAnswerStream(interviewId, answer, onChunk, onDone, onQuestionChunk, signal) {
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
  let buffer = ''              // feedback/report JSON 原文累积缓冲（现有）
  let questionBuffer = ''      // 题目 JSON 原文累积缓冲（新增）

  let currentEvent = ''
  let scoreData = null
  let nextQuestionData = null

  // 兜底超时：上游 LLM 偶发挂起（DeepSeek 200 后 body 永久不发）时 SSE 无任何事件，
  // 180s 内未收到 done 则 abort 并抛明确错误，避免前端永久"思考中"
  let streamTimedOut = false
  const overallTimer = setTimeout(() => {
    streamTimedOut = true
    controller.abort()
  }, 180000)

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
              case 'question_chunk':
                questionBuffer += data.content
                const qText = extractQuestionText(questionBuffer)
                if (qText && onQuestionChunk) onQuestionChunk(qText)
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
  } catch (e) {
    if (e.name === 'AbortError' && streamTimedOut) {
      throw new Error('面试响应超时，AI 暂无响应，请刷新后重试')
    }
    if (e.message !== 'Unexpected end of JSON input') throw e
  } finally {
    clearTimeout(overallTimer)
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
