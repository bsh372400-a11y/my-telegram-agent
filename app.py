def generate_video_agnes(prompt, seconds=5, aspect_ratio="16:9"):
    if not AGNES_API_KEY:
        return None, "Agnes API key missing"
    
    headers = {
        "Authorization": f"Bearer {AGNES_API_KEY}",
        "Content-Type": "application/json"
    }
    
    model = "agnes-video-2.5-flash"
    
    try:
        create_url = "https://apihub.agnes-ai.com/v1/videos"
        payload = {
            "model": model,
            "prompt": prompt,
            "seconds": str(seconds),
            "mode": "text",
            "size": "720P",
            "aspect_ratio": aspect_ratio
        }
        r = httpx.post(create_url, json=payload, headers=headers, timeout=60)
        
        if r.status_code != 200:
            return None, f"Agnes create: {r.text[:150]}"
        
        data = r.json()
        vid = data.get("video_id")
        if not vid:
            return None, f"Agnes no id: {str(data)[:150]}"
        
        poll = f"https://apihub.agnes-ai.com/agnesapi?video_id={vid}&model_name={model}"
        for _ in range(80):
            time.sleep(3)
            try:
                rr = httpx.get(poll, headers={"Authorization": f"Bearer {AGNES_API_KEY}"}, timeout=30)
                if rr.status_code != 200:
                    continue
                d = rr.json()
                st = d.get("status")
                if st == "completed":
                    return d.get("url"), None
                if st == "failed":
                    return None, f"Agnes failed: {d.get('error', 'unknown')}"
            except:
                continue
        return None, "Agnes timeout"
    except Exception as e:
        return None, f"Agnes: {str(e)[:150]}"
