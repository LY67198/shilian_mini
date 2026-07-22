"""双 Swagger 应用配置包

对外暴露 client / backoffice 两套独立 FastAPI 文档应用的元数据、tags、
安全方案等配置，由 app.configs.docs_apps.create_*_app() 工厂消费。
"""