# 产物与结果

Project 运行时文件位于 `TEXT_TO_CAD_PROJECTS_ROOT` 指定的目录（默认 `output/projects`）。
典型 Project 结构如下：

```text
<project-id>/
├── project.json
├── prompt.txt
├── code/
│   ├── model.py
│   └── helpers.py
├── conversation.jsonl
├── events.jsonl
├── diagnostics.json
├── previews/live/
│   ├── model.glb
│   └── status.json
├── artifacts/
│   ├── model.scene.zip
│   ├── model.step
│   ├── product.json
│   ├── model.semantic.json
│   ├── bom.json
│   ├── assumptions.json
│   ├── validation.json
│   └── source.zip
└── artifacts/v0001/...
```

## Viewer 结果

`model.scene.zip` 是 Three.js Viewer 使用的规范 Scene Artifact。后端会在开放它之前检查
schema、成员哈希和渲染资源。`model.step` 是内部桥接产物，也是可下载的产品文件，不是
Agent 的源码事实来源。

## 产品包

`product.json` 描述 `part` 或 `assembly` 结果及其内容寻址文件，可能包含：

- `model.semantic.json`：Assembly 结构和 Part 定义；
- `parts/<part-id>.step`：每个独立制造 Part 的 STEP；
- `bom.json`：数量和组件路径；
- `assumptions.json`：声明的非关键假设；
- `validation.json`：确定性检查及证据；
- `source.zip`：Python 源码快照。

接受后，产物会复制到版本化的 `artifacts/vNNNN/`，`current.json` 指向当前版本。每个
Project 默认保留十个 Accepted 版本，可通过 `CADFLOW_ARTIFACT_VERSION_LIMIT` 修改。

## Trace 与下载

对话和进度记录以 JSONL 保存在 Project 内。API 提供有限的 Trace 查看和脱敏 NDJSON 下载；
返回记录前会脱敏凭证。较大的工具结果单独保存，以限制对话上下文大小。
