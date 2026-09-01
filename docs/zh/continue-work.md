# 继续修改或重试

Accepted 结果是一个检查点，而不是冻结的导出文件。在同一个 Project 中发送新消息请求
修改时，旧版本仍会保留，新的 Run 会单独验证。

## 继续一个成功的 Project

用现有几何描述变化，例如“在保持安装包络不变的情况下增加两个 6 mm 沉孔”。Agent 会
读取当前 `/code/model.py`，修改源码并验证新的 Draft 产品包；只有通过的结果才会成为
下一次 Accepted 版本。

## 从失败中恢复

重试前先阅读失败原因和 Run Progress 详情。常见原因包括模型凭证缺失、Python 无效、布尔
操作失败、solid 无效、约束残差、包络超限或超时。修正需求或环境后发送新消息。后端不会
静默接受部分导出或未验证的 Scene。

**Stop Run** 会请求取消当前 turn。Stopped Run 的记录仍保留，之后可以发送新消息。

## 清空或删除

**Clear Conversation** 在确认名称后重置 Project 的对话，不会改变 Project ID。**Delete
Project** 会永久删除 Project 目录及其产物。删除前请备份需要保留的本地数据。

## 在 Viewer 外查看源码

Viewer 有意不提供源码编辑器或本地 ZIP 选择器。请直接查看磁盘上的 `code/` 和版本化
`source/` 目录，或通过 API 下载脱敏 Trace 和产品文件。
