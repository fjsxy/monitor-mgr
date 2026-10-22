package platform_test

import (
	"testing"

	"github.com/onsi/ginkgo/v2"
	"github.com/onsi/gomega"
)

// TestPlatform 仅接入 go test；全部行为场景由 Ginkgo It/By 执行并生成报告。
func TestPlatform(t *testing.T) {
	gomega.RegisterFailHandler(ginkgo.Fail)
	ginkgo.RunSpecs(t, "一期平台契约")
}
