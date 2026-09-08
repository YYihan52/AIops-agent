# 已知问题清单（Known Issues）

本服务是一个演示用的"遗留系统"：master 上长期并存若干已知、待修复的问题（一部分是
QA / 运营报过、一直没排上修复的）。修 bug 的约定：**一个 PR 只修一个问题**，不要顺手
重构、也不要捎带修别的问题；修完后让该问题的回归测试转绿，且不给其它用例引入新的
失败。

跑全量回归：`pytest -q`。当前下面 7 个问题的回归用例全是红的（`pytest -q` 会看到 8 个
FAILED，其中分页问题占 2 个用例）。

| # | 症状 | 位置 | 怎么复现 / 观测 | 对应回归测试 |
|---|------|------|-----------------|--------------|
| 1 | 推荐结果排序反了：冷门商品排在最前面、热门商品反而靠后 | `ranking.py` `rank_by_score()` | `curl localhost:8080/recommend`，返回把冷门 SKU 排在了前面 | `test_ranking.py` |
| 2 | 一次推荐响应里，同一个商品 ID 出现了两次（`PRODUCT-3` 重复） | `dedupe.py` `dedupe_ids()` | `curl localhost:8080/recommend` | `test_dedupe.py` |
| 3 | 并发请求下分类计数丢更新：压 10000 次请求，分类命中计数只统计到几千 | `stats.py` `record_hit()` | 多线程并发调 `record_hit()` 后看总数（回归测试里已内置这个并发复现） | `test_stats.py` |
| 4 | 目录分页翻页错位：第一页就把最前面的商品漏掉了 | `pagination.py` `paginate()` | `curl localhost:8080/catalog/1/2`，第一页应返回目录前 2 个商品，实际返回的是后面一批 | `test_pagination.py` |
| 5 | 热门位配置更新后 `/trending` 一直不刷新：说好的 60s TTL 形同虚设，缓存条目永不过期 | `cache.py` `TTLCache.set()` | 往缓存里放一个短 TTL 的条目，过了 TTL 再 `get` 仍能取到旧值 | `test_cache.py` |
| 6 | 会员价在 0.5 分的边界上差一分钱：按口径应四舍五入（half-up），实际被直接舍掉 | `pricing.py` `member_price_cents()` | `curl localhost:8080/price/PRODUCT-2/50`，应为 500 分，实际 499 分 | `test_pricing.py` |
| 7 | 服务内存随流量单调上涨、趋向 OOM（最近一次发版之后开始出现） | `recommendation_server.py` 里"已服务商品 ID 历史"这个结构 | `docker stats` 看容器 RSS 持续爬升；`curl localhost:8080/metrics` 看 `recommendation_seen_ids_total` 只增不减 | `test_recommendation.py::test_memory_is_bounded` |
