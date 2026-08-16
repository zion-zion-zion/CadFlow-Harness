<p align="center">
  <img src="docs/assets/cadflowagent-logo.png" width="168" alt="CadFlowAgent 图标">
</p>

<h1 align="center">CadFlowAgent</h1>

<p align="center">
  <strong>让 CAD Agent 能够构建、验证，并从几何结果中持续进化的运行基础设施。</strong>
</p>

<p align="center">
  把通用大模型变成可审计的 CAD 程序员，并让每次运行<br>
  都能沉淀为评测数据、训练轨迹和更专业的 CAD Agent。
</p>

<p align="center">
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"></a>
  <a href="https://nodejs.org/"><img alt="Node.js 22.19+" src="https://img.shields.io/badge/Node.js-22.19+-5FA04E?logo=nodedotjs&logoColor=white"></a>
  <a href="LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-7C3AED"></a>
  <img alt="Platform Linux x86-64" src="https://img.shields.io/badge/Platform-Linux%20x86--64-FCC624?logo=linux&logoColor=black">
  <img alt="Status Alpha" src="https://img.shields.io/badge/Status-Alpha-F59E0B">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#核心设计">🧭 核心设计</a> ·
  <a href="#项目方向">🗺️ 项目方向</a> ·
  <a href="#系统架构">🏗️ 系统架构</a> ·
  <a href="#快速开始">🚀 快速开始</a> ·
  <a href="#cad-skill-层">🧠 CAD Skills</a>
</p>

---

CadFlowAgent 是面向**参数化 CAD Agent 运行、验证与持续改进**的基础设施。它让通用大模型编写可执行的 CAD 程序，用确定性的几何内核检验结果，并将可观察的运行过程沉淀为评测和未来后训练所需的证据。

它不是一个直接输出网格或图片的 3D 基础模型。大模型在系统中扮演 CAD 程序员：读取领域工作流、编写并修改 Python 源码、调用 CadFlow，再根据可量化的几何反馈继续迭代。这个项目的核心资产是可复用的 Agent–CAD 运行底座，而不是一组固定的模型权重。

> [!IMPORTANT]
> 当前版本是面向可信本机的 Alpha 运行时，每个 Project 聚焦生成一个有效 Solid。规模化数据集与 CAD 专用后训练是项目的发展方向，并不代表仓库已经包含训练完成的专用模型。

<a id="核心设计"></a>

## 🧭 核心设计

### 🧩 以程序作为生成介质

CadFlowAgent 生成的是可读、可修改、可重放的 Python CAD 源码，而不是难以继续编辑的不透明三角网格。尺寸参数、特征顺序和建模意图在生成后仍然可以检查与迭代。

### 📐 以几何内核作为验证器

构建零件的确定性 CAD 内核同时负责判断结果是否正确。Solid 数量、体积、包围盒、拓扑、截面以及 BREP/材料差异等指标，能够提供比“看起来相似”更客观的反馈。

### 🧾 让运行过程成为数据

Prompt、可观察的工具活动、源码版本、执行结果、几何测量和最终产物共同构成可审计轨迹。[Agentic Reconstruction Dataset 示例](examples/agentic_reconstruction_dataset/README.zh-CN.md)展示了如何在不保存隐藏思维链的前提下组织 CAD 工具调用与结果记录。

### 🔌 让智能模型可以替换

Deep Agents 与 Pi SDK Sidecar 共享同一套 Project 工作区和 Artifact 契约。模型、Agent Harness 与 CAD Skills 可以持续演进，而执行、验证、运行记录和可视化能力保持复用——这正是 CadFlowAgent 与单一 Agent Demo 的本质区别。

<a id="项目方向"></a>

## 🗺️ 项目方向

| 阶段 | 目标 | 当前状态 |
| --- | --- | --- |
| Agent 运行时 | 驱动通用大模型在交互式工作区中编写、执行、检查并修复单零件 CadFlow 程序。 | ✅ 当前可用 |
| 数据集与评测 | 规模化整理经过验证的建模/重建轨迹、失败与修复、几何指标和最终产物。 | 🌱 已有种子示例，计划规模化 |
| CAD 后训练 | 使用验证轨迹开展监督微调、偏好学习和基于几何结果的奖励训练。 | 🧭 路线规划 |

项目希望形成一条清晰的飞轮：**更可靠的运行时 → 更高质量的轨迹 → 更强的评测与训练数据 → 更专业的 CAD Agent**。

<a id="系统架构"></a>

## 🏗️ 系统架构

~~~mermaid
flowchart TB
    U["自然语言 CAD 任务"]

    subgraph L1["1 · 交互层"]
        UI["Web 工作区<br/>项目控制 + 3D Viewer"]
        API["Project / Run API"]
        UI --> API
    end

    subgraph L2["2 · Agent 层"]
        C["Run Coordinator"]
        H["Agent Harness<br/>Deep Agents | Pi"]
        K["CAD Skills<br/>建模 · 检查 · 验证"]
        C --> H
        K --> H
    end

    subgraph L3["3 · 确定性 CAD 层"]
        W["Project 工作区<br/>model.py"]
        E["CadFlow<br/>几何内核"]
        G["几何 + Scene<br/>验证"]
        W --> E --> G
    end

    subgraph L4["4 · 产物与学习信号"]
        A["Canonical Scene Artifact"]
        T["运行事件、源码<br/>与几何指标"]
        D["数据集 + 评测记录<br/>（项目方向）"]
        T -. "规模化整理" .-> D
    end

    U --> UI
    API --> C
    H --> W
    G -->|验证通过| A
    G -. "可量化的修复证据" .-> H
    A --> UI
    C -. "进度 + 预览" .-> UI
    H --> T
    G --> T
~~~

### 🔄 一次建模运行

1. 用户创建 Project，并提交一份完整的 CAD 任务。
2. 所选 Agent Harness 只读取相关 Skills，并编写入口稳定的 <code>model.py</code>。
3. CadFlow 执行程序；结果无效或不符合要求时，几何检查会返回可量化证据。
4. 通过验证的结果被转换为 Viewer 使用的 <code>model.scene.zip</code>；可观察的运行记录和产物则可继续用于分析与未来的数据整理。

<a id="快速开始"></a>

## 🚀 快速开始

### 环境要求

- Linux x86_64
- Python <code>3.12</code>
- [<code>uv</code>](https://docs.astral.sh/uv/)
- Node.js <code>22.19</code> 或更高版本，以及 npm
- <code>/usr/bin/bwrap</code> 路径下可用的 bubblewrap
- OpenAI 兼容的模型端点和 API Key

仓库内置 <code>vendor/cadflow-0.1.0-cp312-cp312-linux_x86_64.whl</code>，SHA256 为：

~~~text
d48acda48f29f5c022695c377f7e0f6089c188923091fd45c3fd2c0e3234886a
~~~

### 安装

~~~bash
git clone https://github.com/zion-zion-zion/CadFlowAgent.git
cd CadFlowAgent

cp .env.example .env
uv sync --group dev
npm --prefix viewer ci
npm --prefix pi-sidecar ci
npm --prefix pi-sidecar run build
~~~

在 <code>.env</code> 中配置模型服务：

~~~dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
~~~

### 启动

~~~bash
./run.sh
~~~

浏览器打开 [http://localhost:5173](http://localhost:5173)，即可创建 Project、选择可用的 Agent Harness、提交建模任务、查看实时进度并检查最终 3D 结果。后端 API 位于 <code>http://localhost:8000</code>。

### 运行时配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| <code>OPENAI_BASE_URL</code> | — | OpenAI 兼容模型服务的 Base URL。 |
| <code>OPENAI_MODEL_ID</code> | — | Agent Harness 使用的模型标识符。 |
| <code>OPENAI_API_KEY</code> | — | 模型服务凭证，严禁提交到仓库。 |
| <code>TEXT_TO_CAD_HOST</code> | <code>0.0.0.0</code> | 后端监听地址。 |
| <code>TEXT_TO_CAD_PORT</code> | <code>8000</code> | 后端端口。 |
| <code>TEXT_TO_CAD_FRONTEND_HOST</code> | <code>0.0.0.0</code> | Viewer 监听地址。 |
| <code>TEXT_TO_CAD_FRONTEND_PORT</code> | <code>5173</code> | Viewer 端口。 |
| <code>TEXT_TO_CAD_PROJECTS_ROOT</code> | <code>output/projects</code> | Project 工作区的持久化根目录。 |

## 📐 当前建模契约

每个 Project 都从一个稳定入口开始：

~~~python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    # Agent 会将该骨架替换为用户请求的零件。
    raise NotImplementedError
~~~

当前应用只接受一个有效的 <code>cad.Shape</code>，其中必须恰好包含一个 Solid 且体积为正。验证通过后，后端生成供浏览器 Viewer 使用的 <code>artifacts/model.scene.zip</code>。Skills 和独立示例覆盖了更广泛的检查与导出工作流，但不会扩大当前应用边界。

<a id="cad-skill-层"></a>

## 🧠 CAD Skill 层

Skills 提供任务专用的建模知识和精确 API 参考，避免每次 Agent 运行都加载整本 CAD 手册。

| Skill | 关注范围 |
| --- | --- |
| [<code>cadflow-model-part</code>](skills/cadflow-model-part/SKILL.md) | 参数化刚性零件、Sketch、特征、布尔操作、圆角和单零件交付。 |
| [<code>cadflow-flexible-model</code>](skills/cadflow-flexible-model/SKILL.md) | 静态布料、皮革、薄膜、服装及其他柔性几何体。 |
| [<code>cadflow-step-brep</code>](skills/cadflow-step-brep/SKILL.md) | STEP/BREP 检查、特征推断、重建和基于证据的对比。 |
| [<code>cadflow-validate-export</code>](skills/cadflow-validate-export/SKILL.md) | 几何验证、重放、渲染、导出检查和量化报告。 |

## 🗂️ 仓库结构

~~~text
CadFlowAgent/
├── backend/          Agent 运行时、Project API、执行、事件与验证
├── viewer/           浏览器工作区与 Three.js Scene Viewer
├── pi-sidecar/       Pi Agent Harness 的常驻 Worker
├── skills/           渐进式 CadFlow 工作流与 API 参考
├── examples/         零件、柔性模型、装配体与重建数据
├── tests/            运行时、边界、修复循环与集成测试
└── vendor/           平台特定的 CadFlow Wheel
~~~

可以从[示例目录](examples/README.md)探索机械零件、复杂装配体、柔性几何和重建轨迹种子。

## 📄 许可证

CadFlowAgent 使用 [GNU Affero General Public License v3.0](LICENSE) 开源许可证。
