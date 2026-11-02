# 使用说明
# 用途：整话题采集策略常量；已选话题保留原包与原字段。
# 用法：内部配置模块，由 Recorder/采集目录调用；通道选择在网页或 record-start --topics 指定。
# 详细说明：采集与预览使用说明.md

"""定义整话题采集范围；已选话题逐包保存，不删除字段、不插帧。

当前订阅范围不含深度图。温度等原报文字段仍保留在 /raw/* 和
原始 protobuf 负载中；网页检查器显示哪些字段不影响落盘内容。
"""

PROFILE='raw_wire_v3'
EXCLUDED_TOPICS={'/camera/head_depth'}
POLICY={'processing':'none; store received packet bytes directly',
        'profile':PROFILE,'excluded_topics':sorted(EXCLUDED_TOPICS),
        'excluded_fields':[],
        'image_policy':'original RGB JPEG bytes; no transcoding; depth is not subscribed',
        'protobuf_policy':'original protobuf payload bytes; no field removal or reserialization',
        'raw_semantics':'/raw/* is byte-identical to the received Aorta packet, including all original fields'}

