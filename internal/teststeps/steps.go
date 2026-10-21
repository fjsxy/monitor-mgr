// Package teststeps 为 Ginkgo 的连续操作步骤保留一期稳定子例标识。
package teststeps

import (
	"strings"

	"github.com/onsi/ginkgo/v2"
)

// path 只由当前 spec 的同步 By 步骤访问；每个 Ginkgo 进程都有独立副本。
var path []string

// Step 执行一个 By 步骤，完成全部断言后才向 Ginkgo 原生报告追加子例标识。
// 连续步骤共享前置状态，例如先撤销角色再禁用账号，不能拆为随机顺序的独立 It。
func Step(name string, body func()) {
	path = append(path, name)
	defer func() { path = path[:len(path)-1] }()
	ginkgo.By(name, func() {
		body()
		// 关键：失败/Skip 会在此之前中断，不能给未完成的输入签发通过标识。
		selector := ginkgo.CurrentSpecReport().LeafNodeText + "/" + strings.Join(path, "/")
		ginkgo.AddReportEntry("完成子例", selector, ginkgo.ReportEntryVisibilityNever)
	})
}
