# CadFlowAgent

CadFlowAgent 是一个可信的本机 Text-to-CAD 工作区。用户创建一个 Project，提交一次
完整的零件描述，Deep Agent 使用 CadFlow 编写并验证 Python Model Source，
然后由 Viewer 加载 canonical Scene Artifact。

## 环境要求

- Linux x86_64（glibc 2.31 或更高版本）+ Python 3.12，或 macOS 26
  arm64 + Python 3.13
- 用于安装缺失工具的 `curl` 或 `wget`
- 将 `.env.example` 复制为 `.env` 并填写模型配置

仓库为两个受支持的平台分别内置了 CadFlow wheel。`uv` 会通过同一份
`pyproject.toml` 和 `uv.lock` 自动选择匹配的文件：

- Linux：`cadflow-0.1.0-cp312-cp312-linux_x86_64.whl`（SHA256
  `753c513fee879258a561efa9d3edf7e73ebe904ed160264caf5851c20b99854f`）
- macOS：`cadflow-0.1.0-cp313-cp313-macosx_26_0_arm64.whl`（SHA256
  `738bcccab01a8152831a871f3103790feb4d975c1e98357b887fc4ebe56391fa`）

克隆仓库后运行一次安装脚本。脚本会检测当前平台，在缺失时安装 `uv` 和兼容的
Node.js，按锁文件同步 Python 与 Viewer 依赖，并在不覆盖已有文件的前提下从示例
创建 `.env`。

```bash
./setup.sh
```

使用 `./setup.sh --check` 可以只检查现有环境，不进行任何修改。

## 启动

```bash
./run.sh
```

`run.sh` 会自动检测操作系统和架构，并选择对应的 Python 解释器。

默认后端地址是 `127.0.0.1:8765`，Viewer 地址是 `127.0.0.1:5678`。
如需让其他机器访问本机 Viewer，可设置 `TEXT_TO_CAD_HOST`；该服务仍只适合
可信的本机演示环境。

## Skills 与 Model Source

Agent 自动发现 `skills/` 下的 `SKILL.md` 工作流，根据描述选择与任务相关的
skill，并仅在需要时读取完整说明。CadFlow 工作流和 API 引用以这些 skill 文件
为准。

当前应用仍采用更窄的输出契约：每个 Project 先创建一个不会通过验证的
`model.py` 初始骨架，并保留以下稳定入口：

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    # Agent 会将此骨架替换为用户请求的零件。
    raise NotImplementedError
```

Agent 拥有 Project 工作区，可以创建本地模块，也可以使用环境中已安装的
CadFlow/Python API。后端不会根据 API 来源直接拒绝代码，但最终返回值仍必须是
一个有效的 `cad.Shape`，包含一个 solid 且体积为正。后端验证 Shape 后，内部生成
`artifacts/model.scene.zip`，STEP 只用于 Scene 桥接，不作为项目产物。Deep Agent
运行可以只读访问 repository Skills，但 repository examples 不会暴露给 Agent；其中
不同的输入或输出类型不会改变当前 Project 契约。

## 检查

```bash
# Linux
uv run --python 3.12 pytest

# macOS
uv run --python 3.13 pytest

cd viewer && npm run build
```

真实模型服务测试通过 `-m live_agent` 显式启用。
