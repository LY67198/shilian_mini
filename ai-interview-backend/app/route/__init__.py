"""路由注册中心入口

暴露 create_app 应用工厂及 router_registry 路由注册表，
所有 API 路由通过 router_registry.register_routes() 集中挂载。
"""

from app.route.route import create_app