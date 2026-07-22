"""API 路由聚合层 — 客户端 / 后台两套接口拆分

子包：
- client: 用户端 API（无需认证 / OAuth2PasswordBearer 鉴权）
- backoffice: 管理端 API（JWT scope=backoffice）
- docs_export.py: OpenAPI JSON 导出
"""