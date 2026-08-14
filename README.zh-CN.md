# CadFlowAgent

CadFlowAgent 是一个可信的本机 Text-to-CAD 工作区。用户创建一个 Project，提交一次
完整的零件描述，Deep Agent 使用 CadFlow 编写并验证 Python Model Source，
然后由 Viewer 加载 canonical Scene Artifact。

## 环境要求

- Linux x86_64
- Python 3.12
- `uv` 和 Node.js/npm
- 将 `.env.example` 复制为 `.env` 并填写模型配置

仓库内置 `vendor/cadflow-0.1.0-cp312-cp312-linux_x86_64.whl`，SHA256 为
`d48acda48f29f5c022695c377f7e0f6089c188923091fd45c3fd2c0e3234886a`。

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

## Skills 与 Model Source

Agent 自动发现 `skills/` 下的 `SKILL.md` 工作流，根据描述选择与任务相关的
skill，并仅在需要时读取完整说明。CadFlow 工作流和 API 引用以这些 skill 文件
为准。

当前应用仍采用更窄的执行契约：每个 Project 生成一个刚性单零件 `model.py`，
入口如下：

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    return model.box(width=20.0, depth=30.0, height=10.0)
```

生成代码只能使用 CadFlow 文档化的 `Model`/`Shape` API。后端验证 Shape 后，
内部生成 `artifacts/model.scene.zip`，STEP 只用于 Scene 桥接，不作为项目产物。
展示柔性网格、可重放图或装配体的 skills 与 examples 属于 SDK 参考，不会扩大
当前 Project 契约。

## 检查

```bash
uv run pytest
cd viewer && npm run build
```

真实模型服务测试通过 `-m live_agent` 显式启用。
