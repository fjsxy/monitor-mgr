// 本文件将集群当前/候选凭据封装为绑定实体身份的 AES-GCM 密文。
package control

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/json"
	"io"
)

// sealCredential 使用独立随机 nonce 加密凭据，不把明文放入操作历史。
// 关键：AAD 绑定 clusterId 或 operationId，避免密文被移用到其他集群或候选操作。
func (s *Store) sealCredential(c *targetCredential, aad string) ([]byte, error) {
	block, e := aes.NewCipher(s.Key)
	if e != nil {
		return nil, e
	}
	g, e := cipher.NewGCM(block)
	if e != nil {
		return nil, e
	}
	nonce := make([]byte, g.NonceSize())
	if _, e = io.ReadFull(rand.Reader, nonce); e != nil {
		return nil, e
	}
	raw, e := json.Marshal(c)
	if e != nil {
		return nil, e
	}
	return g.Seal(nonce, nonce, raw, []byte(aad)), nil
}

// openCredential 校验密文及其所属身份，只向管控服务内部返回解密材料。
func (s *Store) openCredential(data []byte, aad string) (*targetCredential, error) {
	block, _ := aes.NewCipher(s.Key)
	g, _ := cipher.NewGCM(block)
	if len(data) < g.NonceSize() {
		return nil, Error("UNAVAILABLE", "集群凭据不可用")
	}
	raw, e := g.Open(nil, data[:g.NonceSize()], data[g.NonceSize():], []byte(aad))
	if e != nil {
		return nil, Error("UNAVAILABLE", "原密钥无法解密集群凭据")
	}
	var c targetCredential
	if json.Unmarshal(raw, &c) != nil {
		return nil, Error("UNAVAILABLE", "集群凭据不可用")
	}
	return &c, nil
}
