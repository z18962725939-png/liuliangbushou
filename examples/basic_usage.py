"""
Basic synchronous usage example.

Prerequisites:
    pip install httpx python-dotenv
    # Set JUSTONEAPI_TOKEN in .env or environment

Run:
    python examples/basic_usage.py
"""

from justoneapi_collector import JustOneAPICollector


def main():
    client = JustOneAPICollector()  # loads token from .env

    # --- Taobao product search ---
    resp = client.taobao_and_tmall.product_search_v1(keyword="iPhone 15", page=1)
    if resp.success:
        print(f"Taobao search: {len(resp.data.get('list', [])) if resp.data else 0} items")
    else:
        print(f"Taobao error: [{resp.code}] {resp.message}")

    # --- JD product details ---
    resp = client.jdcom.product_details_v1(item_id="100012043978")
    if resp.success:
        print(f"JD product: {resp.data.get('title', 'N/A') if resp.data else 'N/A'}")
    else:
        print(f"JD error: [{resp.code}] {resp.message}")

    # --- Douyin hot search ---
    resp = client.douyin_tiktok_china.hot_search_v1()
    if resp.success:
        items = resp.data.get('list', []) if resp.data else []
        print(f"Douyin hot: {len(items)} items")
        for item in items[:3]:
            print(f"  - {item.get('word', item.get('title', 'N/A'))}")

    # --- Xiaohongshu note search ---
    resp = client.xiaohongshu_rednote.note_search_v2(keyword="美食", page=1)
    if resp.success:
        print(f"XHS search: success")
    else:
        print(f"XHS error: [{resp.code}] {resp.message}")

    # --- List available platforms ---
    platforms = client.list_platforms()
    print(f"\nAvailable platforms: {len(platforms)}")
    for p in platforms[:5]:
        print(f"  {p['name']} ({p['slug']}) — {p['category']}")
    print(f"  ... and {len(platforms) - 5} more")

    client.close()


if __name__ == "__main__":
    main()
