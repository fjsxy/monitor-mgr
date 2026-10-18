// Agent辅助来源独立保存成功快照与失败时间；主CR成功不能刷新PVC或关联来源。
package control

import (
	"context"
	"encoding/json"
	"sort"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// saveAgentSource 保留组件接缝，所有来源共用同一历史与新鲜度语义。
func (s *Store) saveAgentSource(ctx context.Context, i *rpc.ObservationInstance, source string, current *rpc.SourceObservation, cause error) *rpc.SourceObservation {
	return s.saveSourceObservation(ctx, i, source, current, cause)
}
func (s *Store) agentSources(ctx context.Context, i *rpc.ObservationInstance) []*rpc.SourceObservation {
	return s.instanceSources(ctx, i)
}

// associationObservations逐类读取；只有实际CR来源可以保存运行历史。
func (s *Store) associationObservations(ctx context.Context, i *rpc.ObservationInstance, t *Target, cfg *AgentConfig, persist bool) []*rpc.SourceObservation {
	out := []*rpc.SourceObservation{}
	operator, operatorErr := t.agentOperator(ctx, cfg)
	if operatorErr == nil {
		operatorErr = t.verifyOperatorRuntime(ctx, operator, cfg.Associations.ScrapeConfig.Object.Mode != "None")
	}
	for _, kind := range associationKinds {
		source := "association:" + kind.kind
		rule := cfg.associationRules()[kind.kind]
		var items []map[string]any
		var e error
		if rule.Object.Mode == "None" {
			items = []map[string]any{}
		} else if operatorErr != nil {
			e = operatorErr
		} else {
			var namespaces []string
			namespaces, e = t.associationNamespaces(ctx, cfg.Namespace, rule.Namespace)
			if e == nil {
				for _, ns := range namespaces {
					if !operator.visible(ns, false) {
						e = Error("CONFLICT", "采集配置namespace不在Operator可见范围")
						break
					}
					for _, verb := range []string{"get", "list", "watch"} {
						if e = t.subjectAccess(ctx, operator.user, ns, "monitoring.coreos.com", kind.resource, verb); e != nil {
							break
						}
					}
					if e != nil {
						break
					}
				}
			}
			if e == nil {
				items, e = t.matchAssociation(ctx, cfg, kind)
			}
		}
		value := &rpc.SourceObservation{Source: source, Matches: []*rpc.MatchedResource{}}
		if e == nil {
			for _, item := range items {
				meta := objectMap(item, "metadata")
				value.Matches = append(value.Matches, &rpc.MatchedResource{Kind: kind.kind, Namespace: objectString(meta, "namespace"), Name: objectString(meta, "name"), Uid: objectString(meta, "uid")})
			}
			sort.Slice(value.Matches, func(a, b int) bool {
				return value.Matches[a].Namespace+"/"+value.Matches[a].Name < value.Matches[b].Namespace+"/"+value.Matches[b].Name
			})
		}
		if persist {
			value = s.saveAgentSource(ctx, i, source, value, e)
		} else {
			// 关键：草稿预览属于请求修订，不能覆盖已部署CR的来源或最后成功时间。
			value.LastAttemptAt = s.Now().UTC().Format(time.RFC3339Nano)
			if e != nil {
				value.Error = safeSourceError(e)
			} else {
				value.LastSuccessAt = value.LastAttemptAt
			}
		}
		out = append(out, value)
	}
	return out
}
func (s *Store) PreviewAssociations(ctx context.Context, c *rpc.RequestContext, r *rpc.AssociationPreviewRequest) (*rpc.AssociationPreview, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly {
		return nil, Error("FORBIDDEN", "外部实例无平台草稿关联规则")
	}
	if e = rejectUnknownFields(c, "context"); e != nil {
		return nil, e
	}
	if e = rejectUnknownFields(r, "request"); e != nil {
		return nil, e
	}
	if i.Component != AgentComponent {
		return nil, fieldError("component", "仅Agent具有采集关联")
	}
	value, rev, e := readDraft(ctx, s.DB, i.Id)
	if e != nil {
		return nil, e
	}
	if rev != r.DraftRevision {
		return nil, Error("CONFLICT", "草稿修订已变化")
	}
	cfg := value.(*AgentConfig)
	target, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer target.Close()
	sources := s.associationObservations(ctx, i, target, cfg, false)
	_, latest, e := readDraft(ctx, s.DB, i.Id)
	if e != nil {
		return nil, e
	}
	if latest != rev {
		return nil, Error("CONFLICT", "预览期间草稿已变化，请重新读取")
	}
	return &rpc.AssociationPreview{DraftRevision: rev, Sources: sources}, nil
}
func associationConfigFromCR(cr map[string]any) *AgentConfig {
	meta, spec := objectMap(cr, "metadata"), objectMap(cr, "spec")
	cfg := &AgentConfig{Name: objectString(meta, "name"), Namespace: objectString(meta, "namespace")}
	rule := func(prefix string) AssociationRule {
		parse := func(key, empty string) SelectorRule {
			raw, exists := spec[key]
			if !exists || raw == nil {
				return SelectorRule{Mode: empty}
			}
			m, ok := raw.(map[string]any)
			if !ok {
				return SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{}}
			}
			if len(m) == 0 {
				return SelectorRule{Mode: "All"}
			}
			r := SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{}}
			b, _ := json.Marshal(m)
			json.Unmarshal(b, r.Selector)
			return r
		}
		return AssociationRule{Object: parse(prefix+"Selector", "None"), Namespace: parse(prefix+"NamespaceSelector", "Own")}
	}
	cfg.Associations = AgentAssociations{ServiceMonitor: rule("serviceMonitor"), PodMonitor: rule("podMonitor"), Probe: rule("probe"), ScrapeConfig: rule("scrapeConfig")}
	return cfg
}
func (s *Store) refreshAgentAuxiliary(ctx context.Context, i *rpc.ObservationInstance, t *Target, cr map[string]any) {
	s.associationObservations(ctx, i, t, associationConfigFromCR(cr), true)
	volumes, e := s.readAgentVolumes(ctx, i, t)
	s.saveAgentSource(ctx, i, "PVC", &rpc.SourceObservation{Source: "PVC", Volumes: volumes}, e)
}

// readAgentVolumes经完整真实资源图核对并保存原UID卷历史，父资源消失不等于卷消失。
func (s *Store) readAgentVolumes(ctx context.Context, i *rpc.ObservationInstance, t *Target) ([]*rpc.VolumeObservation, error) {
	return s.observeAgentVolumes(ctx, i, t)
}
