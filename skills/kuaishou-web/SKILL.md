# 快手分享页

读公开的移动端分享页 `c.kuaishou.com/fw/photo/<id>`，从 `window.INIT_STATE` 里取出作品和 mp4。不调用第三方解析站，也不包含 GPL 爬虫代码。

机房 IP 上 GraphQL 经常直接要验证码。这个 Skill 先读分享页里已经渲染出来的数据；页面没有作品对象时返回 `UPSTREAM_CHANGED`，页面要求验证码时返回 `RATE_LIMITED`。

可选环境变量 `KUAISHOU_COOKIES`，需要在路由器里授权给 `example.kuaishou-web`。

单独运行：向 `entry.py resolve` 或 `download` 的 stdin 写 skillcall 请求 JSON。
