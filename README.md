# 政策通 · 中小企业政策助手

一个「答案可溯源」的垂直场景 RAG 知识助手：上传政策文档后，用自然语言提问，AI 基于文档内容回答，**每条回答都标注来源文件 + 页码/章节，点击可查看原文并高亮命中片段**。

代码结构通用：换到别的垂直场景（如"农业补贴助手""园区招商政策助手"），只需替换知识库文档和首页示例问题，代码不用改。

---

## 一、技术栈

| 层 | 技术 |
|---|---|
| 前端 | 原生 HTML + CSS + JavaScript 单页应用（无框架） |
| 后端 | Python + FastAPI |
| 向量库 | Chroma（本地持久化） |
| 文档解析 | pypdf（PDF）+ python-docx（Word） |
| 大模型 | OpenAI 兼容接口（默认 DeepSeek），密钥只存后端 |
| 中文向量 | 阿里云百炼 text-embedding-v3（在线） |
| 流式输出 | SSE（Server-Sent Events） |

---

## 二、目录结构

```
new-chat/
├── backend/                  # 后端
│   ├── main.py               # FastAPI 入口：上传/问答/匹配/反馈/统计接口，SSE 流式
│   ├── parser.py             # PDF/Word 解析 + 按 500 字切分（带页码/章节元数据）
│   ├── embedding.py           # 向量适配层（在线中文向量 / 本地占位回退）
│   ├── vectorstore.py        # Chroma 持久化：入库 / 列表 / 删除
│   ├── retriever.py          # Top-K 检索 + 问答/匹配提示词组装
│   ├── llm.py                # DeepSeek 流式 / 非流式客户端（单例）
│   ├── stats.py               # 提问计数 / 反馈 / 看板统计（JSONL 本地存储）
│   ├── requirements.txt      # 后端依赖
│   ├── .env.example          # 环境变量模板（复制为 .env 后填写）
│   └── .env                  # 真实密钥（私有，不要提交 Git）
├── frontend/                 # 前端（纯静态，无构建）
│   ├── index.html            # 页面结构 + 后端地址配置位
│   ├── style.css            # 样式（白底 / 沉稳蓝 #2563EB / 淡黄高亮 #FEF3C7）
│   └── app.js               # SSE 解析、流式渲染、引用溯源、上传、反馈、版本切换、看板
└── sample_docs/              # 示例政策 PDF（可直接上传试用）
```

---

## 三、本地运行

### 1. 启动后端

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

复制环境变量模板并填写密钥：

```bash
copy .env.example .env    # Windows；Linux/Mac 用 cp
```

编辑 `.env`（见下一节）。然后启动：

```bash
uvicorn main:app --reload --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即成功，健康检查：打开 <http://127.0.0.1:8000/api/health> 应返回 `{"status":"ok"}`。

### 2. 打开前端

直接用浏览器打开 `frontend/index.html`，或在 `frontend` 目录起一个静态服务（推荐，避免个别浏览器对 file:// 的限制）：

```bash
# 任选其一
npx serve frontend
# 或 VSCode 的 Live Server 插件右键打开 index.html
```

本地默认连接 `http://127.0.0.1:8000`，无需改配置。

### 3. 使用流程

1. 点右上角「文档管理」，上传 PDF / Word 政策文件，等待"已索引 N 个片段"。
2. 在输入框自然语言提问，AI 流式作答；点回答下方引用卡片，右侧侧边栏显示原文并高亮命中段。
3. 「政策匹配」：填企业画像（行业/规模/地区/年限/诉求），AI 推荐适用政策清单 + 匹配理由。
4. 「数据看板」：查看累计提问、高频问题 Top5、有用/无用反馈、文档数。

---

## 四、环境变量（backend/.env）

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 对话密钥，申请：<https://platform.deepseek.com/> |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com`，一般不改 |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash`（快、便宜） |
| `DASHSCOPE_API_KEY` | 阿里云百炼向量密钥，申请：<https://bailian.console.aliyun.com/> → API-KEY 管理 |
| `DASHSCOPE_BASE_URL` | 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EMBED_MODEL` | 默认 `text-embedding-v3` |
| `EMBED_DIM` | 默认 `1024` |

> 密钥只放在后端 `.env`，前端绝不出现密钥；所有 AI 调用都由后端代理转发。

---

## 五、部署（Vercel 前端 + Render 后端）

### 1. 后端部署到 Render

1. 把 `backend/` 推到 Git 仓库。
2. Render 新建 **Web Service**，连接仓库：
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Python 版本**：3.11 或以上。
3. 在 Render「Environment」里逐项填入 `.env` 的密钥（DEEPSEEK_API_KEY、DASHSCOPE_API_KEY 等），不要把 `.env` 提交上去。
4. 部署完成后拿到后端地址，如 `https://your-app.onrender.com`，访问 `/api/health` 确认 ok。

> 注意：Render 免费实例使用临时磁盘，**重启/休眠后 Chroma 索引和本地统计会清空**，需重新上传文档；长期使用需挂载持久磁盘或换外部向量库。

### 2. 前端部署到 Vercel

1. 把 `frontend/`（或整个仓库）推到 Git。
2. Vercel 导入项目，**Root Directory** 选 `frontend`，框架选择 **Other**（纯静态，无需构建命令）。
3. 在 `frontend/index.html` 顶部配置位填入 Render 后端地址：

```html
<script>window.APP_API_BASE = 'https://your-app.onrender.com';</script>
```

4. 部署后即可访问。后端 CORS 已放开所有来源，跨域无需额外配置。

---

## 六、核心机制简述

- **切分**：文档按约 500 字切块、50 字重叠，每块带文件名、页码、章节、原文片段元数据。
- **检索**：问题向量化后在 Chroma 中召回 Top-K（默认 3）最相关片段。
- **生成**：把召回片段 + 提示词交给 LLM，要求只依据资料回答、不编造；SSE 逐字返回。
- **溯源**：召回片段随回答一起推给前端，渲染成引用卡片；点击后侧边栏展示该片段原文并淡黄高亮。
- **降级**：流式中断时自动改用非流式一次性返回完整答案。

## 七、换成别的场景

1. 删掉已索引文档，上传新场景的 PDF/Word。
2. 修改 `frontend/app.js` 里的 `SAMPLE_QUESTIONS`（首页示例问题）。
3. 按需调整 `backend/retriever.py` 里的系统提示词语气，其余检索/流式/溯源代码通用。
