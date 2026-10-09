# Issue tracker：GitHub

本仓库的 issues 和 specs 使用 GitHub Issues，由 `gh` CLI 操作。
在仓库内从 git remote 判断目标仓库；跨目录操作时显式指定 `--repo`。

## 操作约定

- 创建：`gh issue create --title "<标题>" --body-file <正文文件>`。
- 读取：`gh issue view <number> --json number,title,body,labels,comments`。
- 列表：`gh issue list --state open --json number,title,body,labels,assignees`；按需使用 `--label` 和 `--state` 筛选。
- 更新正文：`gh issue edit <number> --body-file <正文文件>`。
- 评论：`gh issue comment <number> --body-file <评论文件>`。
- 添加标签：`gh issue edit <number> --add-label "<label>"`。
- 移除标签：`gh issue edit <number> --remove-label "<label>"`。
- 关闭：`gh issue close <number> --comment "<关闭说明>"`。
- 多行正文先写入文件，再使用 `--body-file`，保留真实换行。

## PR 作为任务入口

**PRs as a request surface: no.**

## Skills 指令的含义

- “publish to the issue tracker”：创建 GitHub issue。
- “fetch the relevant ticket”：读取对应 GitHub issue。

## Wayfinder 操作

- Map：使用带 `wayfinder:map` 标签的单个 issue，正文包含 Notes、Decisions-so-far、Fog。
- 子任务：使用 GitHub sub-issue；创建时指定 `--parent <map-number>`，或通过 `gh issue edit <map-number> --add-sub-issue <child-number>` 关联。使用 `wayfinder:<type>` 标签，type 为 research、prototype、grilling 或 task。
- Sub-issues 不可用时：在 Map 正文维护任务列表，子任务正文顶部写入 `Part of #<map-number>`。
- 阻塞关系：使用原生 issue dependencies；通过 `gh issue edit <child-number> --add-blocked-by <blocker-number>` 添加。
- 原生 dependencies 不可用时：在子任务正文顶部记录 `Blocked by: #<number>, #<number>`。所有 blocker 关闭后才可执行。
- Frontier：查询 Map 的开放子任务，排除已有 assignee 或开放 blocker 的任务，按 Map 顺序选择首个任务。原生阻塞状态读取 `issue_dependencies_summary.blocked_by`，大于 0 表示仍有开放 blocker。
- 领取：`gh issue edit <number> --add-assignee @me`，领取后开始处理。
- 完成：评论记录结论，关闭子任务，再向 Map 的 Decisions-so-far 添加结论摘要及链接。
