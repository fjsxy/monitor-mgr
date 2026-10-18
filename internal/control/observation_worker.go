// 后台CR同步复用应用worker生命周期、当前凭据和公共并发预算，不依赖浏览器或用户Session。
package control

import (
	"context"
	"time"
)

// launchObservations 仅在workerLaunch锁内安排任务，退出开始后不增加未登记goroutine。
func (s *Store) launchObservations(life context.Context) {
	rows, e := s.DB.QueryContext(life, `SELECT i.id,i.cluster_id FROM instances i JOIN clusters c ON c.id=i.cluster_id WHERE c.state='ACTIVE' AND i.uid<>'' AND i.lifecycle<>'DELETED'`)
	if e != nil {
		return
	}
	type instance struct{ id, cluster string }
	items := []instance{}
	for rows.Next() {
		var i instance
		if rows.Scan(&i.id, &i.cluster) == nil {
			items = append(items, i)
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return
	}
	for _, i := range items {
		if _, busy := s.observationPolls.LoadOrStore(i.id, true); busy {
			continue
		}
		s.publicationWG.Add(1)
		go func(i instance) {
			defer s.publicationWG.Done()
			defer s.observationPolls.Delete(i.id)
			ctx, cancel := context.WithTimeout(life, 8*time.Second)
			defer cancel()
			target, _, err := s.targetForRecovery(ctx, i.cluster)
			if err != nil {
				if instance, e := instanceByID(ctx, s.DB, i.id); e == nil {
					s.saveSourceObservation(ctx, instance, "CR", nil, err)
				}
				return
			}
			defer target.Close()
			s.refreshObserved(ctx, i.id, target)
		}(i)
	}
}
