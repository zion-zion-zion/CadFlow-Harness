# CadFlow Text-to-CAD Agent

这是一个可信的本机 Text-to-CAD 工作区。用户创建一个 Project，提交一次
完整的零件描述，Deep Agent 使用 CadFlow 编写并验证 Python Model Source，
然后由 Viewer 加载 canonical Scene Artifact。

## 环境要求

- Linux x86_64
- Python 3.12
- `uv` 和 Node.js/npm
- 将 `.env.example` 复制为 `.env` 并填写模型配置

`feat/cadflow` 分支内置
`vendor/cadflow-0.1.0-cp312-cp312-linux_x86_64.whl`，SHA256 为
`28d45c71a3b0e4eb1f77168b984c1600f48823cc0d63f187e4529e30abae3ad8`。

```bash
uv sync --group dev
cd viewer && npm ci
```

## 启动

```bash
./run.sh
```

默认后端地址是 `127.0.0.1:8000`，Viewer 地址是 `127.0.0.1:5173`。
如需让其他机器访问本机 Viewer，可设置 `TEXT_TO_CAD_HOST`；该服务仍只适合
可信的本机演示环境。

## Model Source

Agent 读取 `skills/cadflow-model-part/`，生成如下契约的单文件 `model.py`：

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    return model.box(width=20.0, depth=30.0, height=10.0)
```

生成代码只能使用 CadFlow 文档化的 `Model`/`Shape` API。后端验证 Shape 后，
内部生成 `artifacts/model.scene.zip`，STEP 只用于 Scene 桥接，不作为项目产物。

## 检查

```bash
uv run pytest
cd viewer && npm run build
```

真实模型服务测试通过 `-m live_agent` 显式启用。
