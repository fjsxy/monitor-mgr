package control

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/json"
	"io"
)

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
