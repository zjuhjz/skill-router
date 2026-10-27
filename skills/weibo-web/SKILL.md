# 微博

用微博自己的访客接口拿到 `SUB` cookie，再读公开内容。不登录。

- 正文链接走 `m.weibo.cn/statuses/show`。视频在 `page_info.urls` / `media_info`。图片在 `pics[].large`。
- 电视频链接（`weibo.com/tv/show/1034:…`、`video.weibo.com`）走 `h5.video.weibo.com/api/component`。
- 内容不存在返回 `NOT_FOUND`。访客身份被拒绝返回 `NEED_AUTH`。可选 `WEIBO_COOKIES`，授权给 `example.weibo-web`。

`image.download@1` 只处理带图片的正文。构建机上没有一条可长期引用的图片样本，这条能力的体检是关掉的，解析逻辑有单测。
