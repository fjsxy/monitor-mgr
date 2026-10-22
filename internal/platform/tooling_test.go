package platform_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"

	ginkgo "github.com/onsi/ginkgo/v2"
)

var _ = ginkgo.It("UT-18 测试结果来源与清理边界", func() {
	t := ginkgo.GinkgoT()
	checkBrowserCleanupReadAfterArchive(t)
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

// I29-R02.confirmed：浏览器独立会话注销集群后，原Python管理员仍能只读回查精确操作。
func checkBrowserCleanupReadAfterArchive(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I29-R02：原管理员Session在注销后读取同一部分失败再重试成功的发布资源")
	p, admin, id, target := ksmPublishedFixture(t)
	target.mu.Lock()
	target.rejectSM = 403
	target.mu.Unlock()
	accepted := lifecyclePublish(t, p, admin, id)
	oid := accepted["operationId"].(string)
	awaitLifecycleState(t, p, admin, oid, "FAILED")
	target.mu.Lock()
	target.rejectSM = 0
	target.mu.Unlock()
	retried := clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, admin, oid), admin, "cleanup-retry", 202)
	before := awaitLifecycleState(t, p, admin, oid, "SUCCEEDED")
	if retried["versionId"] != accepted["versionId"] {
		t.Fatal("重试不能改变原发布版本")
	}
	cluster := readJSON(t, p, "/instances/"+id, admin)["clusterId"].(string)
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", nil, admin, "cleanup-unregister", 200)
	target.mu.Lock()
	writes, smWrites := target.writes, target.smWrites
	target.mu.Unlock()
	after := clusterCall(t, p, "GET", "/operations/"+oid, nil, admin, "", 200)
	if after["operationId"] != oid || after["instanceId"] != id || after["versionId"] != accepted["versionId"] || after["clusterId"] != cluster || after["action"] != "PUBLISH" || !reflect.DeepEqual(after["resources"], before["resources"]) {
		t.Fatal("注销后必须用原Session回读同一已确认资源")
	}
	for _, raw := range after["resources"].([]any) {
		if raw.(map[string]any)["confirmedUid"] == "" {
			t.Fatal("成功资源缺少已确认UID")
		}
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.writes != writes || target.smWrites != smWrites {
		t.Fatal("清理身份回读不能重放资源写入")
	}
}
