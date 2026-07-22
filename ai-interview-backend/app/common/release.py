
"""客户端版本发布配置 — 维护窗口 / 强制升级 / 版本号

供 /api/v1/config/release 端点返回给前端，前端据此决定是否弹升级提示。
"""

RELEASE_CONFIG = {
    'maintenance' : {
        'is_maintenance' : 0,
        'start_time' : 1583401241,
        'end_time' : 1583402241
    },
    'release' : {
        'hotfix_version_code' : 1000000,
        'logout' : 0,
        'version' : '0.1.0',
        'version_code' : 1000000,
        'force_upgrade' : 1000000
    }
}