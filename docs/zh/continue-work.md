# 继续修改或重试

Accepted 版本会保留在 Project 中。你可以在同一个 Project 发送新消息，旧版本不会被覆盖，新 Run 会单独检查修改后的结果。

## 继续一个成功的 Project

直接描述相对于现有几何的变化，例如“保持安装包络不变，增加两个 6 mm 沉孔”。Agent 读取当前 `/code/model.py`，修改源码并生成新的 Draft 产品包；检查通过后才会成为下一次 Accepted 版本。

## 从失败中恢复

重试前先查看失败原因和 Run Progress。常见原因包括模型凭证缺失、Python 无效、布尔操作失败、solid 无效、约束残差、包络超限或超时。修正需求或环境后发送新消息。后端不会接受未完成导出或未通过验证的 Scene。

**Stop Run** 会请求取消当前 turn。Stopped Run 的记录仍会保留，之后可以发送新消息。

## 清空或删除

**Clear Conversation** 在确认名称后重置 Project 的对话，不会改变 Project ID。**Delete Project** 会永久删除 Project 目录及其产物。删除前请备份需要保留的本地数据。

## 在 Viewer 外查看源码

Viewer 不提供源码编辑器或本地 ZIP 选择器。请直接查看磁盘上的 `code/` 和版本化 `source/` 目录，或通过 API 下载脱敏 Trace 和产品文件。
