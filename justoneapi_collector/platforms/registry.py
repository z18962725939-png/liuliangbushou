"""
Platform and endpoint registry.

This module contains the complete metadata for all 40 platforms and ~320
API endpoints covered by Just One API. The registry is a plain data
structure so that platform methods can be auto-generated at import time
and the CLI can enumerate available platforms/endpoints dynamically.

Each endpoint tuple: (path_slug, http_method, description)
The method name is auto-derived by converting path_slug hyphens to underscores.
"""

from __future__ import annotations

# Category constants
CAT_ECOMMERCE = "ecommerce"
CAT_SOCIAL = "social"
CAT_CREATOR = "creator_marketplace"
CAT_REAL_ESTATE = "real_estate"
CAT_ENTERTAINMENT = "entertainment"
CAT_AI = "ai"
CAT_STOCK_PHOTO = "stock_photo"

# fmt: off
# Each entry: (path_slug, http_method, description_zh)
PLATFORMS: list[dict] = [
    # ===================================================================
    # 1. 电商类平台
    # ===================================================================
    {
        "slug": "1688",
        "name": "阿里巴巴1688",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-details-v1", "GET", "通过商品ID获取1688批发商品的公开详情，用于产品采购或目录研究时审查已知报价"),
            ("product-search-v1", "GET", "通过关键词搜索1688批发商品列表，用于采购和市场研究时发现候选产品或供应商"),
        ],
    },
    {
        "slug": "aliexpress",
        "name": "AliExpress速卖通",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "搜索AliExpress商品，支持查询词、页码、排序、类别、品牌、位置、属性、价格、语言环境、地区和货币控制"),
            ("product-overview-v1", "GET", "通过商品ID获取美国或全球站点上的AliExpress商品概览"),
        ],
    },
    {
        "slug": "amazon",
        "name": "亚马逊Amazon",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "通过关键词或ASIN搜索亚马逊市场商品，支持国家、排序、商品状况、Prime专享、优惠和分页控制"),
            ("product-details-v1", "GET", "通过ASIN获取指定国家市场中亚马逊商品的详情"),
            ("product-top-reviews-v1", "GET", "通过ASIN获取指定国家市场中亚马逊商品的热门评论"),
            ("best-sellers-v1", "GET", "获取指定国家市场中按分类路径的分页亚马逊热销商品"),
            ("products-by-category-v1", "GET", "通过数字分类节点在选定的国家市场中获取分页亚马逊商品"),
        ],
    },
    {
        "slug": "taobao-and-tmall",
        "name": "淘宝和天猫",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-details-v1", "GET", "V1接口，按商品ID获取淘宝或天猫商品详情"),
            ("product-details-v2", "GET", "从控制台任务管理页面访问异步淘宝和天猫商品详情工作流"),
            ("product-details-v3", "GET", "V3接口，按商品ID获取淘宝或天猫商品详情（有页面已售，无价格）"),
            ("product-details-v4", "GET", "V4接口，按商品ID获取淘宝或天猫商品详情（有最终价格）"),
            ("product-details-v5", "GET", "V5接口，按商品ID获取淘宝或天猫商品详情（有缓存）"),
            ("product-details-v6", "GET", "推荐V6接口，按商品ID获取详情，包括预估销量、运费、是否包邮及部分商品券后价"),
            ("product-details-v7", "GET", "V7接口，按商品ID获取淘宝或天猫商品详情（支持所有商品ID）"),
            ("product-details-v8", "GET", "V8接口，按item ID检索详情，提供准确的SKU数量信息"),
            ("product-details-v9", "GET", "V9接口，按商品ID获取淘宝或天猫商品详情"),
            ("product-reviews-v3", "GET", "通过商品ID以分页和可配置排序方式获取淘宝和天猫的商品评价"),
            ("product-questions-v1", "GET", "通过商品ID获取淘宝或天猫商品社交动态，支持基于页面的分页"),
            ("shop-product-list-v1", "GET", "通过卖家或用户ID获取淘宝或天猫店铺的商品，支持分页和默认或销量排序"),
            ("shop-product-list-v2", "GET", "使用卖家或用户ID和店铺ID获取商品，支持分页和按销量、新品、信用或价格排序"),
            ("shop-product-list-v3", "GET", "通过卖家ID和店铺ID检索商品，支持分页浏览和可配置排序（包括新品排序）"),
            ("shop-product-list-v4", "GET", "通过卖家ID获取商品，支持基于页面的分页"),
            ("product-search-v1", "GET", "通过关键词搜索淘宝和天猫商品，支持分页、排序、可选仅天猫和包含价格范围筛选"),
            ("product-search-v2", "GET", "按关键词搜索，使用基于页码的分页方式，返回按销量排序的结果"),
            ("product-sales-v1", "GET", "通过商品ID检索淘宝或天猫商品的30天销量"),
        ],
    },
    {
        "slug": "jdcom",
        "name": "京东JD.com",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-details-v1", "GET", "通过已知商品ID获取京东商品详情"),
            ("product-details-v2", "GET", "从控制台任务管理页面访问异步京东商品详情工作流"),
            ("product-details-v3", "GET", "通过已知商品ID检索京东商品详情，用于产品研究或报价比较"),
            ("product-details-v4", "GET", "通过商品ID获取详情，包括完整的商品图片集"),
            ("product-price-v1", "GET", "通过已知商品ID获取京东商品当前价格"),
            ("product-search-v1", "GET", "通过关键词搜索京东商品，支持分页"),
            ("product-search-v2", "GET", "通过关键词搜索京东商品，支持分页并按销量排序"),
            ("product-comments-v1", "GET", "获取按商品ID标识的京东商品的分页买家评论"),
            ("product-comments-v2", "GET", "获取指定SKU的京东商品分页评论"),
            ("shop-product-list-v1", "GET", "获取按店铺ID标识的京东店铺的一页商品"),
        ],
    },
    {
        "slug": "xianyu-goofish",
        "name": "闲鱼Goofish",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "通过关键词搜索闲鱼二手商品，支持分页和排序控制"),
            ("product-details-v1", "GET", "通过商品ID获取闲鱼二手商品的公开详情"),
        ],
    },
    {
        "slug": "dewu-poizon",
        "name": "得物Poizon",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "通过关键词搜索得物商品，支持分页"),
        ],
    },
    {
        "slug": "shopee",
        "name": "Shopee",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("item-details-v1", "GET", "通过市场站点和商品ID获取Shopee商品详情"),
            ("item-details-v2", "GET", "使用V2数据源，通过市场站点、店铺ID和商品ID获取Shopee商品详情"),
            ("shop-profile-and-rating-summary-v1", "GET", "通过市场站点和店铺ID获取Shopee店铺资料和评分摘要"),
            ("shop-item-list-v1", "GET", "通过所选市场站点中的店铺用户名获取Shopee商品卡片"),
            ("item-sku-matrix-v1", "GET", "获取所选市场站点中Shopee商品的SKU选项矩阵"),
            ("shop-basic-profile-v1", "GET", "通过用户名和市场站点获取Shopee店铺基本资料"),
            ("item-search-v1", "GET", "在所选市场站点中按关键词搜索Shopee商品"),
            ("item-sku-models-v1", "GET", "检索Shopee商品的SKU模型，提供偏移量和评分筛选"),
            ("shop-details-v1", "GET", "根据用户名和市场站点检索详细的Shopee店铺信息"),
            ("rich-shop-reviews-v1", "GET", "使用从零开始的结果偏移量检索详细的Shopee店铺评价"),
            ("item-display-snapshot-v1", "GET", "检索所选市场站点中Shopee商品的展示快照"),
            ("shop-reviews-v1", "GET", "检索Shopee店铺评价，提供偏移分页和评分筛选"),
            ("item-reviews-v1", "GET", "检索指定Shopee商品的评价，提供偏移分页和评分筛选"),
            ("shop-categories-v1", "GET", "使用从零开始的结果偏移量检索Shopee店铺分类"),
            ("item-review-model-distribution-v1", "GET", "获取Shopee商品在SKU模型上的评价分布"),
            ("item-review-tags-v1", "GET", "获取指定市场站点中Shopee商品的评价标签"),
            ("search-category-facets-v1", "GET", "获取Shopee商品搜索关键词的分类面"),
            ("item-installments-and-amounts-v1", "GET", "获取Shopee商品的分期和金额信息，支持台湾和印度尼西亚"),
        ],
    },
    {
        "slug": "temu",
        "name": "Temu",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("homepage-product-feed-v1", "GET", "检索指定站点的Temu首页商品信息流卡片，支持偏移分页"),
            ("full-product-details-v1", "GET", "按商品ID检索指定站点的Temu完整商品详情，可选择包含SKU数据"),
            ("product-core-snapshot-v1", "GET", "按商品ID检索指定站点的Temu商品核心快照"),
            ("category-top-sales-v1", "GET", "检索指定站点指定类目下的Temu热销商品，支持页码分页"),
            ("product-category-tree-v1", "GET", "检索指定站点的Temu商品类目树"),
            ("product-review-list-v1", "GET", "按商品ID检索指定站点的Temu商品评价，支持页码分页"),
            ("store-review-list-v1", "GET", "通过店铺ID和站点，使用页码分页和排序类型检索Temu店铺评价"),
            ("product-review-summary-v1", "GET", "通过商品ID和站点检索Temu商品评价摘要"),
            ("store-product-list-v1", "GET", "通过店铺ID和站点，使用页码分页检索Temu店铺商品"),
            ("keyword-product-search-v1", "GET", "通过关键词和站点，使用偏移分页搜索Temu商品"),
        ],
    },
    {
        "slug": "tiktok-shop",
        "name": "TikTok Shop",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "在选定区域内通过关键词搜索TikTok Shop商品，提供偏移和页面令牌分页"),
            ("product-details-v1", "GET", "获取选定区域内商品ID的公开TikTok Shop商品详情"),
            ("product-details-v2", "GET", "获取抖音店铺商品详情数据，包括标题、描述和价格"),
        ],
    },
    {
        "slug": "xiaohongshu-e-commerce-rednote",
        "name": "小红书电商",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("product-search-v1", "GET", "按关键词搜索小红书电商商品，使用分页和搜索ID分页"),
        ],
    },
    {
        "slug": "douyin-e-commerce",
        "name": "抖音电商",
        "category": CAT_ECOMMERCE,
        "endpoints": [
            ("item-details-v1", "GET", "获取抖音电商商品详情，包括价格、标题和库存"),
            ("item-details-v2", "GET", "通过商品ID获取抖音电商商品详情"),
            ("product-sku-info-v1", "GET", "通过商品ID获取抖音电商商品的SKU信息"),
            ("product-sku-info-v2", "GET", "通过商品ID获取抖音电商商品的SKU信息，不支持时返回202"),
            ("product-search-v1", "GET", "通过关键词以分页和搜索ID分页方式搜索抖音电商商品"),
            ("item-comments-v1", "GET", "通过商品ID以分页方式获取抖音电商商品的客户评论"),
            ("shop-product-list-v1", "GET", "通过店铺ID以分页方式获取抖音电商店铺的商品列表"),
        ],
    },
    # ===================================================================
    # 2. 创作者市场类平台
    # ===================================================================
    {
        "slug": "xiaohongshu-creator-marketplace-pugongying",
        "name": "小红书蒲公英创作者市场",
        "category": CAT_CREATOR,
        "endpoints": [
            ("creator-search-v1", "GET", "使用关键词、受众、位置、内容、定价、表现、合作、直播和相似度筛选条件搜索蒲公英创作者"),
            ("creator-profile-v1", "GET", "获取已知用户ID的创作者资料和合作报价信息"),
            ("data-summary-v1", "GET", "获取创作者日常或合作笔记的数据摘要"),
            ("follower-growth-history-v1", "GET", "获取创作者在选定的30天或90天期间内的粉丝历史数据"),
            ("follower-summary-v1", "GET", "检索已知创作者的粉丝概要"),
            ("similar-creators-v1", "GET", "检索与已知创作者相似的创作者分页列表"),
            ("creator-feature-tags-v1", "GET", "检索分配给已知创作者的特征标签"),
            ("creator-content-tags-v1", "GET", "检索与已知创作者关联的内容分类标签"),
            ("note-performance-metrics-v1", "GET", "检索带有商业、笔记类型、日期范围和广告筛选条件的总体笔记表现指标"),
            ("follower-distribution-v1", "GET", "检索已知创作者的粉丝画像数据"),
            ("cost-effectiveness-analysis-v1", "GET", "检索已知创作者的性价比分析"),
            ("note-details-v1", "GET", "检索已知笔记ID的详细信息"),
            ("creator-core-metrics-v1", "GET", "检索带有商业、笔记类型、日期范围和广告筛选条件的创作者核心数据"),
            ("content-square-notes-v1", "GET", "按关键词搜索或浏览所有内容广场笔记，支持商业类别、排序、时间窗口和分页控制"),
            ("creator-note-list-v1", "GET", "检索带有广告、排序方式、笔记类型和第三方平台筛选条件的创作者笔记分页列表"),
            ("creator-note-list-pro-v1", "GET", "检索带有广告、排序方式和笔记类型控制的创作者笔记分页列表"),
            ("get-kol-info-v1", "GET", "获取创作者资料数据，包括受众和定价数据"),
            ("get-kol-note-rate-v1", "GET", "获取笔记表现指标数据，包括核心指标、趋势信号和表现指标"),
            ("get-kol-fans-portrait-v1", "GET", "获取粉丝分布数据，包括受众人口统计、兴趣和分布指标"),
            ("get-kol-fans-summary-v1", "GET", "获取粉丝摘要数据"),
            ("get-kol-fans-trend-v1", "GET", "获取粉丝增长历史数据"),
            ("data-summary-v2", "GET", "获取摘要数据，包括活动、互动和受众增长"),
            ("get-kol-cost-effective-v1", "GET", "获取成本效益分析数据，包括定价、覆盖范围和互动效率指标"),
            ("get-note-detail-v1", "GET", "获取笔记详情数据，包括媒体和互动信号"),
            ("get-kol-core-data-v1", "GET", "获取创作者核心指标数据，包括互动和内容指标"),
        ],
    },
    {
        "slug": "douyin-creator-marketplace-xingtu",
        "name": "抖音巨量星图创作者市场",
        "category": CAT_CREATOR,
        "endpoints": [
            ("creator-search-v1", "GET", "通过关键词和结构化筛选搜索创作者，用于构建和比较活动候选名单"),
            ("creator-profile-v1", "GET", "返回指定创作者和内容渠道的资料"),
            ("creator-link-structure-v1", "GET", "返回指定创作者和内容渠道的创作者链接结构数据"),
            ("creator-visibility-status-v1", "GET", "返回指定创作者是否可在所选内容渠道上展示"),
            ("creator-channel-metrics-v1", "GET", "返回指定创作者和内容形式的渠道信息"),
            ("creator-order-experience-v1", "GET", "返回过去30天或90天的创作者订单体验信息"),
            ("creator-link-metrics-v1", "GET", "返回指定创作者、内容渠道和行业类别的创作者链接指标"),
            ("video-distribution-v1", "GET", "返回指定创作者和内容渠道的视频分发数据"),
            ("audience-distribution-v1", "GET", "返回指定创作者、内容渠道和所选关系阶段的受众分布数据"),
            ("marketing-metrics-v1", "GET", "返回指定创作者和内容渠道的营销信息"),
            ("spread-metrics-v1", "GET", "返回指定创作者的传播指标，可筛选渠道、周期、视频类型、任务和流量来源"),
            ("conversion-analysis-v1", "GET", "返回指定创作者、内容渠道和30天或90天周期的转化分析"),
            ("showcase-items-v1", "GET", "返回指定创作者的展示商品，可筛选内容渠道、任务和流量来源"),
            ("conversion-resources-v1", "GET", "列出指定创作者的转化相关视频或产品"),
            ("cost-performance-analysis-v1", "GET", "返回指定创作者和内容渠道的性价比信息"),
            ("audience-touchpoint-distribution-v1", "GET", "返回指定创作者和内容渠道的受众触点分布数据"),
            ("recommended-videos-v1", "GET", "返回指定创作者和内容渠道的推荐视频"),
            ("follower-distribution-v1", "GET", "返回指定创作者的粉丝或忠实粉丝分布数据"),
            ("item-report-trends-v1", "GET", "返回指定视频作品的报告趋势数据"),
            ("video-details-v1", "GET", "返回指定视频作品的报告详情"),
            ("item-report-analysis-v1", "GET", "返回指定视频作品的报告分析"),
            ("comment-keyword-analysis-v1", "GET", "返回指定创作者的评论关键词分析"),
            ("follower-growth-trend-v1", "GET", "返回指定创作者和时间范围内的每日粉丝趋势数据"),
            ("content-keyword-analysis-v1", "GET", "返回指定创作者的内容关键词分析"),
            ("creator-business-card-v1", "GET", "返回指定创作者的名片资料"),
            ("creator-commerce-spread-info-v1", "GET", "返回指定创作者的电商传播信息"),
            ("creator-homepage-videos-v1", "GET", "列出创作者主页视频，支持关键词、视频类型、任务、日期和分页筛选"),
            ("creator-tags-v1", "GET", "返回指定创作者的标签"),
            ("creator-side-base-info-v1", "GET", "返回指定创作者的侧边栏基础信息"),
            ("live-statistics-v1", "GET", "返回创作者的直播统计数据"),
            ("live-watch-distribution-v1", "GET", "返回创作者和选定直播间类型的直播观众或粉丝观看分布数据"),
            ("commerce-seeding-base-info-v1", "GET", "返回创作者在可选的30天或90天周期内的电商种草基础信息"),
            ("creator-contract-base-info", "GET", "返回创作者在可选的30天或90天周期内的合同相关基础信息"),
            ("creator-search-light-v1", "GET", "获取KOL关键词搜索数据，包括匹配创作者和发现数据"),
            ("get-kol-info-v1", "GET", "获取创作者资料数据，包括受众和定价数据"),
            ("get-kol-audience-distribution-v1", "GET", "获取受众分布数据，包括人口统计和兴趣基础"),
            ("get-kol-fans-distribution-v1", "GET", "获取粉丝分布数据，包括受众人口统计、兴趣和分布指标"),
            ("get-kol-marketing-info-v1", "GET", "获取营销指标数据，包括刊例价详情和商业服务指标"),
            ("get-kol-spread-info-v1", "GET", "获取传播指标数据，包括受众、内容表现和商业指标"),
            ("get-kol-convert-ability-v1", "GET", "获取转化分析数据，包括转化效率和商业性能指标"),
            ("get-kol-show-items-v2-v1", "GET", "获取展示商品数据，包括核心指标、趋势信号和表现指标"),
            ("get-kol-link-info-v1", "GET", "获取创作者链接指标数据，包括创作者排名、流量结构"),
            ("get-kol-convert-videos-or-products-v1", "GET", "获取转化资源数据，包括与创作者转化活动相关的商品"),
            ("get-kol-link-struct-v1", "GET", "获取创作者链接结构数据，包括参与度和转化指标"),
            ("get-kol-touch-distribution-v1", "GET", "获取受众触点分布数据，包括细分群体、受众构成"),
            ("get-kol-cp-info-v1", "GET", "获取成本性能分析数据，包括定价、曝光和参与效率指标"),
            ("get-kol-rec-videos-v1", "GET", "获取推荐视频数据，包括视频表现指标"),
            ("get-kol-daily-fans-v1", "GET", "获取粉丝增长趋势数据，包括历史受众随时间变化"),
            ("get-author-hot-comment-tokens-v1", "GET", "获取KOL评论关键词分析数据"),
            ("get-author-content-hot-keywords-v1", "GET", "获取KOL内容关键词分析数据"),
            ("get-author-commerce-spread-info-v1", "GET", "获取作者商业传播信息数据"),
            ("get-author-commerce-seed-base-info-v1", "GET", "获取作者商业种草基础信息数据"),
            ("get-video-detail-v1", "GET", "获取视频详情数据，包括来自传统星图接口的性能字段"),
        ],
    },
    {
        "slug": "qq-huxuan-creator-marketplace",
        "name": "腾讯互选创作者市场",
        "category": CAT_CREATOR,
        "endpoints": [
            ("video-account-creator-search-v1", "GET", "通过可选昵称关键词和页码分页搜索或浏览QQ互选视频号创作者"),
            ("official-account-creator-search-v1", "GET", "通过可选昵称或账号关键词和页码分页搜索或浏览QQ互选公众号创作者"),
            ("video-account-creator-details-v1", "GET", "检索已知创作者应用ID的QQ互选视频号创作者详细信息"),
            ("video-account-recent-videos-v1", "GET", "列出创作者在所需日期范围内的视频"),
            ("official-account-creator-details-v1", "GET", "检索已知创作者应用ID的QQ互选公众号创作者详细信息"),
            ("official-account-article-list-v1", "GET", "列出创作者在所需日期范围内的文章"),
        ],
    },
    # ===================================================================
    # 3. 社交媒体与内容类平台
    # ===================================================================
    {
        "slug": "social-media",
        "name": "社交媒体搜索",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("cross-platform-search-v1", "GET", "获取跨平台社交媒体搜索数据，包括小红书、抖音、快手、微信、哔哩哔哩、微博和知乎结果"),
        ],
    },
    {
        "slug": "xiaohongshu-rednote",
        "name": "小红书",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("ask-dots-ai", "GET", "使用关键词问题查询小红书的问点点AI，用于检索AI答案"),
            ("hot-search-v1", "GET", "搜索小红书热门内容条目，支持可选关键词、内容类目路径、分页、排序指标和时间范围控制"),
            ("hot-inspiration-feed-v1", "GET", "获取小红书创作者中心热门灵感信息流，支持游标分页"),
            ("note-search-v2", "GET", "按关键词搜索小红书笔记，支持page分页、排序和笔记类型筛选"),
            ("note-search-v4", "GET", "通过移动应用搜索流程搜索小红书笔记，支持分页、排序、笔记类型和时间筛选"),
            ("user-search-v2", "GET", "通过关键词搜索小红书用户，支持基于页面的分页"),
            ("user-published-notes-v4", "GET", "通过用户ID或支持的个人主页URL读取小红书用户已发布的公开笔记，支持lastCursor分页"),
            ("note-details-v1", "GET", "检索由笔记ID或受支持的探索URL标识的小红书笔记详情"),
            ("note-details-v6", "GET", "根据笔记ID获取小红书视频笔记详情"),
            ("note-comments-v2", "GET", "使用游标分页和普通、最新或点赞数排序获取小红书笔记的评论"),
            ("comment-replies-v2", "GET", "使用游标分页获取特定小红书笔记评论的回复"),
            ("user-profile-v3", "GET", "通过用户ID或支持的个人主页URL获取小红书用户资料"),
            ("keyword-suggestions-v1", "GET", "返回针对提交的种子词的小红书搜索关键词建议"),
            ("topic-note-list-v1", "GET", "使用热门或最新排序和游标分页获取与话题ID相关的小红书笔记"),
            ("share-link-resolution-v1", "GET", "解析受支持的小红书短分享链接并返回其公开重定向URL"),
        ],
    },
    {
        "slug": "douyin-tiktok-china",
        "name": "抖音",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("video-search-v4", "GET", "按关键词搜索抖音视频，支持排序、发布时间、时长和分页筛选"),
            ("image-post-search-v1", "GET", "按关键词搜索抖音图片帖子，并使用搜索会话分页"),
            ("hot-search-v1", "GET", "搜索抖音内容，支持可选的关键词、类别、视频类型、排序、分页、互动量和创作者粉丝数筛选"),
            ("user-search-v2", "GET", "按关键词搜索抖音用户，支持基于页面的分页和可选的账号类型筛选"),
            ("user-published-videos-v1", "GET", "检索抖音用户发布的视频，支持游标分页以及最新或最热排序"),
            ("user-published-videos-v3", "GET", "通过游标分页获取抖音用户发布的视频"),
            ("video-details-v2", "GET", "通过视频ID获取抖音视频详情"),
            ("video-comments-v1", "GET", "通过aweme ID以分页方式获取抖音视频的顶级评论"),
            ("comment-replies-v1", "GET", "通过分页方式获取抖音视频顶级评论的回复"),
            ("user-profile-v3", "GET", "通过secUid获取抖音用户资料"),
            ("share-link-resolution-v1", "GET", "解析一个支持的抖音短分享链接并返回其公开重定向URL"),
        ],
    },
    {
        "slug": "kuaishou",
        "name": "快手",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("video-search-v1", "GET", "通过关键词搜索快手公开视频，支持基于游标的分页"),
            ("video-search-v2", "GET", "通过关键词搜索快手公开视频，支持页码分页"),
            ("user-search-v2", "GET", "通过关键词搜索快手公开用户账号，支持页码分页"),
            ("user-published-videos-v2", "GET", "获取快手用户发布的公开视频，支持可选的基于游标的分页"),
            ("video-details-v2", "GET", "获取由视频ID标识的快手公开视频的详细信息"),
            ("video-comments-v1", "GET", "获取快手视频的公开评论，支持基于游标的分页"),
            ("user-profile-v1", "GET", "获取由用户ID标识的快手用户的公开资料"),
            ("share-link-resolution-v1", "GET", "解析支持的快手短分享链接并返回其公开重定向URL"),
        ],
    },
    {
        "slug": "weibo",
        "name": "微博",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("keyword-search-v2", "GET", "获取微博关键词搜索数据，包括作者、发布时间和互动信号"),
            ("post-details-v1", "GET", "获取微博帖子详情数据，包括媒体、作者元数据和互动计数"),
            ("user-profile-v3", "GET", "获取微博用户资料数据，包括粉丝数、验证状态和简介详情"),
            ("user-followers-v1", "GET", "获取微博用户粉丝数据，包括资料元数据和验证信号"),
            ("user-following-v1", "GET", "获取微博用户关注者数据"),
            ("user-published-posts-v1", "GET", "获取微博用户发布帖子数据"),
            ("user-video-list-v1", "GET", "获取微博用户视频列表数据（瀑布流）"),
            ("tv-video-details-v1", "GET", "获取微博TV视频详情数据"),
            ("hot-search-v1", "GET", "获取微博热搜数据，包括排名数据"),
            ("post-comments-v1", "GET", "获取微博帖子评论数据"),
            ("search-user-published-posts-v1", "GET", "获取微博搜索用户发布帖子数据"),
        ],
    },
    {
        "slug": "bilibili",
        "name": "哔哩哔哩",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("video-details-v2", "GET", "检索由BVID标识的哔哩哔哩视频详情"),
            ("user-published-videos-v2", "GET", "检索由UID标识的哔哩哔哩用户发布的视频"),
            ("user-profile-v2", "GET", "检索由UID标识的哔哩哔哩用户资料"),
            ("video-danmaku-v2", "GET", "检索由AID和CID标识的哔哩哔哩视频片段的单页弹幕评论"),
            ("video-comments-v2", "GET", "检索由AID标识的哔哩哔哩视频的评论，支持可选的游标分页"),
            ("video-search-v2", "GET", "按关键词搜索哔哩哔哩视频，支持基于页面的分页"),
            ("share-link-resolution-v1", "GET", "解析指向视频的受支持哔哩哔哩短分享链接"),
            ("user-relation-stats-v1", "GET", "检索由WMID标识的哔哩哔哩用户的关系统计"),
            ("video-captions-v2", "GET", "检索由BVID、AID和CID标识的哔哩哔哩视频片段的字幕数据"),
        ],
    },
    {
        "slug": "zhihu",
        "name": "知乎",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("column-article-details-v1", "GET", "获取知乎专栏文章详情数据，包括标题、作者和内容"),
            ("answer-list-v1", "GET", "获取知乎回答列表数据，包括回答内容、作者资料和互动指标"),
            ("keyword-search-v1", "GET", "获取知乎关键词搜索数据，包括匹配结果、元数据和排名信号"),
            ("column-article-list-v1", "GET", "获取知乎专栏文章列表数据"),
        ],
    },
    {
        "slug": "wechat-official-accounts",
        "name": "微信公众号",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("account-historical-articles-v1", "POST", "通过用户名检索微信公众号的历史文章，使用游标分页"),
            ("account-historical-articles-v2", "POST", "获取由ghid或文章URL标识的微信公众号的历史文章"),
            ("article-metrics-v1", "GET", "获取以#rd或#wechat_redirect结尾的微信公众号文章URL的基本交互指标"),
            ("article-metrics-v2", "GET", "通过URL获取微信公众号文章的扩展交互指标"),
            ("article-details-v1", "GET", "通过URL获取微信公众号文章，可选择纯文本或富文本内容模式"),
            ("article-details-v2", "GET", "通过URL获取微信公众号文章的结构化详情"),
            ("article-details-v3", "GET", "获取可能包含视频内容的微信公众号文章的详情"),
            ("article-details-v4", "GET", "通过URL获取微信公众号文章的HTML表示"),
            ("article-details-v5", "GET", "通过URL获取微信公众号文章的轻量信息"),
            ("article-comments-v1", "POST", "通过URL获取微信公众号文章的顶级评论"),
            ("article-comment-replies-v1", "GET", "获取微信公众号文章顶级评论下的回复"),
            ("article-search-v1", "POST", "按关键词搜索微信公众号文章，支持发布时间、排序筛选和分页"),
            ("article-search-v2", "POST", "在指定类别内按关键词搜索微信公众号文章，支持连续分页"),
            ("mini-program-search-v1", "POST", "按关键词搜索微信小程序，支持分页"),
            ("wechat-index-search-v1", "GET", "查询微信指数的关键词数据"),
            ("search-suggestions-v1", "GET", "获取关键词的微信搜索建议"),
            ("account-search-v1", "POST", "按关键词搜索微信公众号，支持页码分页"),
            ("account-search-v2", "POST", "按关键词搜索微信公众号，支持页码、偏移量和连续状态分页"),
            ("account-original-article-count-v1", "GET", "获取由ghid或文章URL标识的微信公众号的原创文章数量"),
            ("account-principal-info-v1", "GET", "获取微信公众号的主体信息"),
            ("account-basic-info-v1", "GET", "通过账号名称获取微信公众号的基本资料信息"),
            ("article-link-conversion-v1", "GET", "将微信公众号文章链接扩展为解析后的长链接"),
        ],
    },
    {
        "slug": "wechat-channels",
        "name": "微信视频号",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("video-search-v1", "POST", "按关键词搜索微信视频号视频，支持发布时间、排序、页码、偏移量和连续状态控制"),
            ("video-search-v2", "POST", "按关键词搜索微信视频号的视频类别"),
            ("account-search-v1", "POST", "按关键词搜索微信视频号的账号类别，支持分页"),
            ("account-search-v2", "POST", "通过直接视频号查询按关键词搜索微信视频号账号相关记录"),
            ("account-search-v3", "POST", "通过关键词查找特定的微信视频号账号身份"),
            ("account-videos-v1", "POST", "检索按v2Name分页的微信视频号账号动态"),
            ("get-bound-channel-v1", "GET", "使用其原始ID或文章URL查找绑定到微信公众号的微信视频号账号"),
            ("video-basic-info-v1", "GET", "解析复制的微信视频号视频链接以获取基本视频信息"),
            ("video-title-v1", "GET", "通过对象ID和可选随机数ID检索微信视频号视频的轻量级标题信息"),
            ("export-id-conversion-v1", "GET", "将搜索结果中的加密微信视频号导出ID转换为可用的视频对象标识"),
            ("video-download-url-v1", "GET", "通过对象ID和可选随机数ID检索微信视频号视频的可下载媒体URL"),
            ("video-metrics-v1", "POST", "通过对象ID检索微信视频号视频的互动指标"),
            ("video-comments-v1", "POST", "通过对象ID和连续缓冲分页检索微信视频号视频的一级评论"),
            ("video-sub-comments-v1", "POST", "使用视频和根评论ID检索微信视频号视频一级评论下的回复"),
        ],
    },
    {
        "slug": "toutiao",
        "name": "今日头条",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("article-details-v1", "GET", "通过文章ID获取今日头条文章的详情"),
            ("video-details-v1", "GET", "获取通过视频ID标识的今日头条视频的详细信息"),
            ("user-profile-v1", "GET", "通过用户ID获取今日头条用户资料"),
            ("user-id-v1", "GET", "解析与今日头条个人主页URL关联的用户ID"),
            ("web-keyword-search-v2", "GET", "按关键词搜索今日头条网页文章"),
        ],
    },
    {
        "slug": "youtube",
        "name": "YouTube",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("general-search-v1", "GET", "按关键词搜索YouTube视频，支持可选的语言、上传日期、时长和排序筛选条件"),
            ("video-details-v1", "GET", "通过视频ID获取YouTube视频的公开详情"),
            ("channel-videos-v1", "GET", "从YouTube频道获取公开视频，可选基于游标的分页"),
            ("video-captions-v1", "GET", "获取YouTube视频的可用字幕轨道，或以SRT、XML、JSON3或纯文本格式请求字幕"),
            ("channel-shorts-v1", "GET", "通过YouTube频道ID检索公开的Shorts，支持可选的continuation-token分页"),
            ("video-comment-list-v1", "GET", "获取YouTube视频的一级评论，支持排序、区域设置选项和游标分页"),
            ("video-sub-comment-list-v1", "GET", "获取与YouTube评论延续游标关联的回复"),
        ],
    },
    {
        "slug": "tiktok",
        "name": "TikTok",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("user-published-posts-v1", "GET", "获取由secUid标识的TikTok用户发布的帖子，提供游标分页和最新或最热门排序"),
            ("post-details-v1", "GET", "获取由帖子ID标识的TikTok帖子详情"),
            ("user-profile-v1", "GET", "通过公开用户名或secUid获取TikTok用户资料"),
            ("post-comments-v1", "GET", "获取TikTok帖子的评论，提供游标分页"),
            ("comment-replies-v1", "GET", "获取TikTok帖子上特定评论的回复，提供游标分页"),
            ("post-search-v1", "GET", "通过关键词搜索TikTok帖子，提供偏移分页、排序、发布时间和区域控制"),
        ],
    },
    {
        "slug": "twitter",
        "name": "Twitter / X",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("search-timeline-v1", "GET", "按关键词搜索X(Twitter)，支持热门、最新、媒体、用户或列表结果模式，使用游标分页"),
            ("user-profile-v1", "GET", "通过数字Rest ID检索X(Twitter)用户资料"),
            ("user-published-posts-v1", "GET", "通过Rest ID检索X(Twitter)用户发布的帖子，使用游标分页"),
            ("post-comments-v1", "GET", "通过推文ID检索X(Twitter)帖子的最新评论，使用游标分页"),
            ("post-detail-v1", "GET", "获取由推文ID标识的X(Twitter)帖子的完整详情"),
        ],
    },
    {
        "slug": "facebook",
        "name": "Facebook",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("post-search-v1", "GET", "通过关键词搜索公开Facebook帖子，可选包含日期范围筛选和游标分页"),
            ("get-profile-id-v1", "GET", "解析与提交的个人资料路径值关联的Facebook个人资料ID"),
            ("get-profile-posts-v1", "GET", "使用游标分页获取指定Facebook个人资料ID的公开帖子"),
            ("post-comments-v1", "GET", "使用游标分页获取指定Facebook帖子ID的评论"),
            ("comment-replies-v1", "GET", "使用旧版帖子ID、评论ID和扩展令牌检索特定Facebook帖子评论的回复"),
        ],
    },
    {
        "slug": "instagram",
        "name": "Instagram",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("user-profile-v1", "GET", "获取Instagram用户资料数据，包括粉丝数、关注数和帖子数"),
            ("post-details-v1", "GET", "获取Instagram帖子详情数据，包括帖子标题、媒体内容和发布时间"),
            ("user-published-posts-v1", "GET", "获取Instagram用户发布帖子数据"),
            ("reels-search-v1", "GET", "获取Instagram Reels搜索数据"),
            ("hashtag-post-search-v1", "GET", "获取Instagram标签帖子搜索数据"),
        ],
    },
    {
        "slug": "linkedin",
        "name": "LinkedIn领英",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("people-search-v1", "GET", "通过姓名、职位、公司、学校、地点、语言、行业或服务类别搜索LinkedIn人员"),
            ("user-profile-v1", "GET", "通过用户名获取LinkedIn资料"),
            ("company-employees-v1", "GET", "获取与公司ID关联的LinkedIn人员分页"),
            ("user-published-posts-v1", "GET", "获取由LinkedIn用户URN发布的帖子，支持分页和分页令牌"),
            ("post-reactions-v1", "GET", "获取帖子的LinkedIn反应记录，可选择按反应类型和分页筛选"),
            ("post-comments-v1", "GET", "获取帖子的一页LinkedIn评论，支持按相关性或最新排序"),
        ],
    },
    {
        "slug": "reddit",
        "name": "Reddit",
        "category": CAT_SOCIAL,
        "endpoints": [
            ("post-details-v1", "GET", "获取Reddit帖子详情数据，包括作者详情、子版块信息和互动计数"),
            ("post-comments-v1", "GET", "获取Reddit帖子评论数据，包括文本、作者和时间戳"),
            ("keyword-search-v1", "GET", "获取Reddit关键词搜索数据，包括标题、作者和子版块上下文"),
        ],
    },
    # ===================================================================
    # 4. 房产类平台
    # ===================================================================
    {
        "slug": "beike",
        "name": "贝壳",
        "category": CAT_REAL_ESTATE,
        "endpoints": [
            ("ershoufang_detail_v1", "GET", "获取贝壳二手房详情数据，包括定价（总价和单价）、物理属性（面积和布局）"),
            ("get_ershoufang_list_v1", "GET", "获取贝壳二手房列表数据，支持按城市/地区、价格范围和布局过滤"),
            ("community_list_v1", "GET", "获取贝壳小区列表数据，包括小区名称和唯一ID以及平均挂牌价格和历史价格趋势"),
        ],
    },
    # ===================================================================
    # 5. 文娱类平台
    # ===================================================================
    {
        "slug": "douban-movie",
        "name": "豆瓣电影",
        "category": CAT_ENTERTAINMENT,
        "endpoints": [
            ("movie_reviews_v1", "GET", "获取豆瓣电影或电视剧目分页长评，按时间或热度排序"),
            ("review_details_v1", "GET", "通过评论ID获取单条豆瓣长评"),
            ("subject_details_v1", "GET", "通过剧目ID获取豆瓣电影或电视剧目的公开详情页数据"),
            ("comments_v1", "GET", "获取豆瓣电影或电视剧目的分页短评，按时间或最新评分排序"),
            ("recent_hot_movie_v1", "GET", "获取豆瓣近期热门合集中当前展示的电影分页列表"),
            ("recent_hot_tv_v1", "GET", "获取豆瓣近期热门合集中当前展示的电视剧分页列表"),
        ],
    },
    {
        "slug": "imdb",
        "name": "IMDb",
        "category": CAT_ENTERTAINMENT,
        "endpoints": [
            ("release_expectation_v1", "GET", "获取IMDb标题上映预期数据，包括制作状态、上映日期和预期信号"),
            ("extended_details_v1", "GET", "获取IMDb标题扩展详情数据，包括标题信息、图像和类型"),
            ("cast_crew_v1", "GET", "获取IMDb标题主要演员和工作人员数据"),
            ("basic_info_v1", "GET", "获取IMDb标题基本信息数据，包括标题文本、上映年份和类型"),
            ("redux_overview_v1", "GET", "获取IMDb标题Redux概览数据"),
            ("did_you_know_insights_v1", "GET", "获取IMDb标题'你知道吗'洞察数据"),
            ("critic_reviews_summary_v1", "GET", "获取IMDb标题影评人评论摘要数据"),
            ("awards_summary_v1", "GET", "获取IMDb标题奖项摘要数据"),
            ("user_reviews_summary_v1", "GET", "获取IMDb标题用户评论摘要数据"),
            ("plot_summary_v1", "GET", "获取IMDb标题剧情摘要数据"),
            ("contributions_v1", "GET", "获取IMDb标题贡献问题数据"),
            ("details_v1", "GET", "获取IMDb标题详情数据，包括元数据、发布信息和演员阵容"),
            ("box_office_summary_v1", "GET", "获取IMDb标题票房摘要数据"),
            ("recommendations_v1", "GET", "获取IMDb标题推荐数据"),
            ("keyword_search_v1", "GET", "获取IMDb关键词搜索数据"),
            ("streaming_highlights_v1", "GET", "获取IMDb流媒体精选数据"),
            ("category_news_v1", "GET", "获取IMDb按类别新闻数据"),
            ("chart_rankings_v1", "GET", "获取IMDb标题榜单排名数据"),
            ("countries_of_origin_v1", "GET", "获取IMDb标题原产国数据"),
        ],
    },
    {
        "slug": "youku",
        "name": "优酷",
        "category": CAT_ENTERTAINMENT,
        "endpoints": [
            ("video_search_v1", "GET", "获取优酷视频搜索数据，包括视频ID、标题和封面图片"),
            ("video_details_v1", "GET", "获取优酷视频详情数据，包括视频ID、标题和描述"),
            ("user_profile_v1", "GET", "获取优酷用户资料数据，包括用户ID、用户名和头像"),
        ],
    },
    # ===================================================================
    # 6. AI 与图库类平台
    # ===================================================================
    {
        "slug": "llm",
        "name": "大模型问答",
        "category": CAT_AI,
        "endpoints": [
            ("doubao_answer_v1", "GET", "为提交的关键词或问题请求豆包网页回答"),
        ],
    },
    {
        "slug": "pixabay",
        "name": "Pixabay",
        "category": CAT_STOCK_PHOTO,
        "endpoints": [
            ("photo_search_v1", "GET", "按关键词搜索公共Pixabay照片结果页，支持请求图片数量和页面控制"),
        ],
    },
    {
        "slug": "vcg",
        "name": "视觉中国VCG",
        "category": CAT_STOCK_PHOTO,
        "endpoints": [
            ("image_search_v1", "GET", "通过关键词搜索VCG图片，支持分页控制、资源ID排除项和可选的500px Select/Prime过滤器"),
        ],
    },
    {
        "slug": "zcool",
        "name": "站酷ZCool",
        "category": CAT_STOCK_PHOTO,
        "endpoints": [
            ("image_search_v1", "GET", "按关键词搜索ZCool社区作品图片，支持请求图片数量和页面控制"),
        ],
    },
]
# fmt: on

# -- Indexes for fast lookup ---------------------------------------------------

_PLATFORM_INDEX: dict[str, dict] = {p["slug"]: p for p in PLATFORMS}
_CATEGORY_INDEX: dict[str, list[dict]] = {}
for p in PLATFORMS:
    _CATEGORY_INDEX.setdefault(p["category"], []).append(p)


def get_platform_meta(slug: str) -> dict | None:
    """Return the full metadata dict for a platform slug, or None."""
    return _PLATFORM_INDEX.get(slug)


def list_platforms(category: str | None = None) -> list[dict]:
    """Return platform metadata list, optionally filtered by category."""
    if category is None:
        return PLATFORMS
    return _CATEGORY_INDEX.get(category, [])


def list_categories() -> list[str]:
    """Return all category strings."""
    return list(_CATEGORY_INDEX.keys())


def get_endpoint_meta(platform_slug: str, endpoint_path: str) -> dict | None:
    """Look up a single endpoint's metadata."""
    platform = _PLATFORM_INDEX.get(platform_slug)
    if not platform:
        return None
    for ep in platform["endpoints"]:
        if ep[0] == endpoint_path:
            return {"path": ep[0], "method": ep[1], "description": ep[2]}
    return None


def path_to_method_name(path_slug: str) -> str:
    """Convert a path slug like 'product-details-v1' to 'product_details_v1'."""
    return path_slug.replace("-", "_")


def method_to_path_name(method_name: str) -> str:
    """Convert a method name like 'product_details_v1' back to 'product-details-v1'."""
    return method_name.replace("_", "-")


def count_endpoints() -> int:
    """Total number of endpoints across all platforms."""
    return sum(len(p["endpoints"]) for p in PLATFORMS)
