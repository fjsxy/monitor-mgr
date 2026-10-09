以第一性原理，从原始需求和问题本质出发，不从惯例或模板出发。
1. 不要假设我清楚自己想要什么。动机或目标不清晰时，停下来讨论。
2. 目标清晰但是路径不是最短的，直接告诉我并建议更好的办法。
3. 遇到问题追根因，不打补丁。每个决策都要能回答“为什么”。
4. 输出说重点，砍掉一切不改变决策的信息。
5. 产物均使用中文，但是产物中的技术名词、代码标识、API/RPC 名称、文件路径、命令等保留英文或原始写法。

<!-- CODEGRAPH_START -->
## CodeGraph

仓库根目录存在 `.codegraph/` 时，理解或定位代码应先使用 CodeGraph，再使用文本搜索或直接读文件：

- MCP：使用 `codegraph_explore` 查询相关符号源码及调用路径；查询中指定文件或符号可读取当前带行号的源码。工具尚未加载时，按名称搜索加载。
- Shell：使用 `codegraph explore "<symbol names or question>"`。

没有 `.codegraph/` 时跳过 CodeGraph；是否建立索引由用户决定。
<!-- CODEGRAPH_END -->

## Agent skills

### Issue tracker

任务使用 GitHub Issues；创建、读取或更新 issue/spec 前，阅读 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个默认 triage labels；判断或修改 triage 状态前，阅读 `docs/agents/triage-labels.md`。

### Domain docs

采用 single-context；探索代码或讨论领域术语、架构决策前，阅读 `docs/agents/domain.md`。
