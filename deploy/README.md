# 部署说明

目标：把服务跑在 Linux 服务器上，通过 HTTPS 对外访问。

---

## 一、部署前必须知道的三件事

### 1. 知识库索引要一起带上去

`backend/kb/data/` 下有四个文件，**缺一不可**：

| 文件 | 大小参考 | 说明 |
|---|---|---|
| `chunks.json` | 小 | 切块结果 |
| `bm25.pkl` | 小 | BM25 索引 |
| `vectors.npy` | 数十 MB | 向量索引 |

**血泪教训（前一项目真实踩过）**：这两套索引必须一起重建。只更新其中一个，
索引与块列表就会错位——检索结果张冠李戴，而且**不会报任何错**。
所以更新知识库时用 `python scripts/build_index.py` 全量重跑，不要手工替换单个文件。

### 2. 索引最好在开发机构建好再传上去

构建向量索引需要下载嵌入模型（约 100MB）并做编码。服务器配置低的话这一步很慢，
而且在内存小的机器上容易被 OOM Kill。

```bash
# 本地构建好
python scripts/build_index.py
# 连同 index 一起打包上传
tar czf kbdata.tar.gz backend/kb/data/
```

服务器上只需要 `pip install -r requirements.txt`，不需要再跑建索引。
（首次运行仍会下载嵌入模型并缓存，所以 `HF_ENDPOINT=https://hf-mirror.com` 必须配。）

### 3. 内存要留够

知识库常驻内存约为索引文件大小的 **1.5~2 倍**。2G 内存的机器务必先加 swap：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 二、部署步骤

```bash
# 1. 建目录与用户
sudo useradd -r -s /usr/sbin/nologin www-data 2>/dev/null || true
sudo mkdir -p /opt/weather-agent && sudo chown $USER /opt/weather-agent
cd /opt/weather-agent

# 2. 传代码（两种方式任选）
#    a) 用 git
git clone <your-repo> .
#    b) 本地打包上传
#    rsync -avz --exclude node_modules --exclude __pycache__ ./ server:/opt/weather-agent/

# 3. 建虚拟环境并装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
vim .env          # 填 API Key，把 LLM_PROVIDER 改成线上要用的

# 5. 传知识库索引（如果服务器上没建过）
#    本地执行：rsync -avz backend/kb/data/ server:/opt/weather-agent/backend/kb/data/

# 6. 前端构建（如果 dist 没一起传）
cd frontend && pnpm install && pnpm build && cd ..

# 7. 注册 systemd 服务
sudo cp deploy/weather-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now weather-agent
sudo systemctl status weather-agent
```

## 三、配置反向代理

**Caddy（推荐，自动 HTTPS）**

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo vim /etc/caddy/Caddyfile     # 改域名
sudo systemctl reload caddy
```

**Nginx + certbot**

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/weather-agent
sudo ln -s /etc/nginx/sites-available/weather-agent /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d weather.example.com
```

---

## 四、验证清单

```bash
# 服务活着
curl -s https://weather.example.com/api/health | python -m json.tool

# 知识库加载了（chunks 不为 0、vector_index 为 true）
# 注意 vector_index 为 false 说明降级成了纯 BM25，检索质量会明显下降

# 流式真的在流式（应该看到内容逐步到达，而不是等十几秒一次性出现）
curl -N -X POST https://weather.example.com/api/chat/stream \
     -H "Content-Type: application/json" \
     -d '{"question":"杭州明天天气怎么样？","mode":"single"}'

# 前端页面能打开
curl -I https://weather.example.com/
curl -I https://weather.example.com/kb      # SPA 深链接，应为 200
```

---

## 五、常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面白屏，控制台报 MIME 类型错误 | 服务器 MIME 配置异常 | 代码里已显式注册 MIME（`backend/main.py`），若仍出现检查是否被中间件改写 |
| 刷新 `/kb` 404 | SPA 兜底路由未生效 | 确认 `frontend/dist/index.html` 存在 |
| 流式输出变成一次性出现 | 反向代理缓冲未关闭 | Caddy 加 `flush_interval -1`；Nginx 加 `proxy_buffering off` |
| 首次启动很慢（几十秒） | 在下载嵌入模型 | 属正常，模型会缓存；确认 `HF_ENDPOINT` 已配 |
| 429 且提示配额 | 供应商额度用尽 | 换供应商（改 `.env` 一行）或充值 |
| 搜索结果与问题无关 | 索引错位 | 用 `build_index.py` **全量重建**，不要手工替换单个索引文件 |
| 进程被 Killed | 内存不足 | 加 swap，或调小 systemd 的 `MemoryMax` 与 `MAX_SESSIONS` |

---

## 六、更新部署

```bash
cd /opt/weather-agent
git pull
.venv/bin/pip install -r requirements.txt      # 依赖有变时

# 知识库有更新时才需要（记得全量重建）
.venv/bin/python scripts/build_index.py

# 前端有改动时
cd frontend && pnpm install && pnpm build && cd ..

sudo systemctl restart weather-agent
```
