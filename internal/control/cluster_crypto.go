package control

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/json"
	"errors"
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

func (s *Store) verifyExistingCredentials() error {
	var exists int
	if s.DB.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'`).Scan(&exists) != nil {
		return errors.New("数据库结构无法读取")
	}
	if exists == 0 {
		return nil
	}
	var version int
	if s.DB.QueryRow(`SELECT coalesce(max(version),0) FROM schema_migrations`).Scan(&version) != nil {
		return errors.New("数据库版本无法读取")
	}
	if version < 3 {
		// 已知 schema1/2 不含集群凭据；不把结构错误当成空库。
		for _, q := range []string{`SELECT id,username,password_hash,role,enabled,must_change,auth_version,created_at FROM accounts LIMIT 0`, `SELECT digest,account_id,auth_version,expires_at FROM sessions LIMIT 0`} {
			r, e := s.DB.Query(q)
			if e != nil {
				return errors.New("旧库结构不兼容，保留原库")
			}
			r.Close()
		}
		return nil
	}
	rows, e := s.DB.Query(`SELECT id,credential FROM clusters WHERE credential IS NOT NULL UNION ALL SELECT operation_id,cipher FROM cluster_candidates`)
	if e != nil {
		return errors.New("凭据结构无法读取")
	}
	defer rows.Close()
	for rows.Next() {
		var aad string
		var data []byte
		if rows.Scan(&aad, &data) != nil {
			return errors.New("凭据结构无法读取")
		}
		if _, e = s.openCredential(data, aad); e != nil {
			return errors.New("原密钥无法解密已保存集群凭据")
		}
	}
	if rows.Err() != nil {
		return errors.New("凭据结构无法读取")
	}
	return nil
}
