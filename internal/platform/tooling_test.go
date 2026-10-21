package platform_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"

	ginkgo "github.com/onsi/ginkgo/v2"
)

var _ = ginkgo.It("UT-18 测试结果来源与清理边界", func() {
	t := ginkgo.GinkgoT()
	// 接下来只运行三组可信边界断言；不再嵌套旧 254 项工具自测。
	ginkgo.By("缺失失败跳过重复事件、错误 candidate、错误清理身份必须拒绝")
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Clean(filepath.Join(filepath.Dir(file), "../.."))
	python := os.Getenv("MONITOR_MGR_PYTHON")
	if python == "" {
		python = "python3"
	}
	cmd := exec.Command(python, filepath.Join(root, "tests/tools/minimal.py"))
	cmd.Dir = root
	if raw, e := cmd.CombinedOutput(); e != nil {
		t.Fatalf("工具边界失败：%v %s", e, raw)
	}
})
