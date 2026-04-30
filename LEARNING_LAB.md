# Learning Lab v2 本地闭环

这个目录是 zenus 个人网站的本地草稿。`learning.html` 可以作为公开静态页面使用，但真正的“写入学习日志”能力只在本地服务开启时可用。

## 启动

```bash
cd /tmp/zenus-site
python3 learning_lab_server.py --port 8766
```

打开：

```text
http://127.0.0.1:8766/learning.html
```

## API

### GET /api/learning-state

读取真实学习状态，来源：

- `/Users/zhouxb/Documents/github/ai-learning/PLAN.md`
- `/Users/zhouxb/Documents/github/english-learning/README.md`
- `/Users/zhouxb/Documents/github/english-learning/daily-log.md`

并写出：

```text
/Users/zhouxb/Documents/github/zenus-os/records/learning-state.json
```

### POST /api/learning-log

提交 Learning Lab 表单后：

- English → 追加到 `english-learning/daily-log.md`，并更新 `english-learning/README.md` 的 Session 数
- AI → 追加到 `ai-learning/logs/YYYY-MM-DD.md`
- Both → 两边都写
- 然后重新生成 `zenus-os/records/learning-state.json`

## 安全边界

- 只建议绑定 `127.0.0.1`，不要暴露到公网。
- 公开网站 `https://zenus-c5p.pages.dev/learning.html` 不会有写入能力，只会生成 Markdown 草稿。
- 真正写入本地仓库前，页面会调用本机 API；如果 API 不存在，自动 fallback 到草稿模式。

## 测试

```bash
cd /tmp/zenus-site
python3 -m unittest test_learning_lab_server.py -v
```

测试使用临时目录，不会修改真实学习仓库。
