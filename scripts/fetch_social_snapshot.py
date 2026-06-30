#!/usr/bin/env python3
"""Write a static social-media snapshot for the Caddy SNS page."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
POSITIVE_RE = re.compile(
  r"\b(bull|bullish|breakout|pump|surge|rally|moon|up|buy|long|growth|adoption|launch|upgrade)\b",
  re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
  r"\b(bear|bearish|dump|crash|down|sell|short|risk|warning|scam|hack|fear|drop|problem)\b",
  re.IGNORECASE,
)


def clean_detail(text: str) -> str:
    return ANSI_RE.sub("", text).strip()


def run_json_lines(command: list[str], timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    try:
      process = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
      return [], str(exc)
    if process.returncode != 0:
      detail = clean_detail(process.stderr or process.stdout or "")
      return [], detail or f"exit {process.returncode}"
    rows = []
    for line in process.stdout.splitlines():
      line = line.strip()
      if not line:
        continue
      try:
        item = json.loads(line)
      except json.JSONDecodeError:
        continue
      if isinstance(item, dict):
        rows.append(item)
    return rows, None


def run_json(command: list[str], timeout: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
      process = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
      return None, str(exc)
    if process.returncode != 0:
      detail = clean_detail(process.stderr or process.stdout or "")
      return None, detail or f"exit {process.returncode}"
    try:
      payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
      return None, str(exc)
    return payload if isinstance(payload, dict) else None, None


def iso_from_timestamp(value: Any) -> str:
    try:
      timestamp = float(value)
    except (TypeError, ValueError):
      return ""
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()


def view_count(item: dict[str, Any]) -> int:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    for key in ("views", "view_count", "impression_count"):
      try:
        value = int(float(metrics.get(key) or 0))
      except (TypeError, ValueError):
        value = 0
      if value > 0:
        return value
    return 0


def mood_summary(items: list[dict[str, Any]]) -> list[str]:
    if not items:
      return [
        "최근 24시간 기준으로 분석할 SNS 콘텐츠가 부족합니다.",
        "조회수 기반 관심 흐름은 아직 뚜렷하게 잡히지 않습니다.",
        "스냅샷을 한 번 더 갱신한 뒤 방향성을 보는 편이 안전합니다.",
      ]

    positive = 0
    negative = 0
    total_views = sum(view_count(item) for item in items)
    platforms = sorted({str(item.get("platform") or "sns") for item in items})
    for item in items:
      text = f"{item.get('title') or ''} {item.get('author') or ''}"
      positive += len(POSITIVE_RE.findall(text))
      negative += len(NEGATIVE_RE.findall(text))

    if positive > negative:
      tone = "긍정/상승 기대 쪽 단어가 더 많이 보입니다."
    elif negative > positive:
      tone = "경계/하락 리스크 쪽 표현이 더 두드러집니다."
    else:
      tone = "긍정과 경계가 섞인 중립적인 흐름입니다."

    lead = items[0]
    lead_title = str(lead.get("title") or "상위 콘텐츠").replace("\n", " ").strip()
    if len(lead_title) > 70:
      lead_title = lead_title[:67] + "..."

    return [
      f"수집 콘텐츠 합산 관심도는 {total_views:,} views이며, 주요 출처는 {', '.join(platforms)}입니다.",
      f"가장 많이 본 콘텐츠는 '{lead_title}'입니다.",
      tone,
    ]


def youtube_rows(query: str, limit: int, cutoff: dt.datetime, timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    if not shutil.which("yt-dlp"):
      return [], "yt-dlp not found"
    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query) + "&sp=CAI%253D"
    rows, error = run_json_lines(
      ["yt-dlp", "--dump-json", "--dateafter", "today-1day", "--playlist-end", str(limit), search_url],
      timeout,
    )
    items = []
    for row in rows:
      created_at = iso_from_timestamp(row.get("timestamp"))
      if created_at:
        parsed = dt.datetime.fromisoformat(created_at)
        if parsed < cutoff:
          continue
      video_id = str(row.get("id") or "")
      url = str(row.get("webpage_url") or "")
      thumbnail = str(row.get("thumbnail") or "")
      if not thumbnail and video_id:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
      items.append(
        {
          "platform": "youtube",
          "id": video_id,
          "title": str(row.get("title") or "YouTube video"),
          "author": str(row.get("channel") or ""),
          "url": url,
          "thumbnail": thumbnail,
          "created_at": created_at,
          "metrics": {"views": row.get("view_count")},
        }
      )
    return items[:limit], error


def x_rows(query: str, limit: int, cutoff: dt.datetime, timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    if not shutil.which("xurl"):
      return [], "xurl not found"
    encoded_query = urllib.parse.quote(query)
    path = (
      "/2/tweets/search/recent"
      f"?query={encoded_query}"
      f"&max_results={max(10, min(100, limit))}"
      "&tweet.fields=created_at,public_metrics,entities,author_id"
      "&expansions=attachments.media_keys,author_id"
      "&media.fields=media_key,preview_image_url,url,type"
      "&user.fields=username,name,profile_image_url"
    )
    payload, error = run_json(["xurl", path], timeout)
    if error or not payload:
      return [], error or "empty X response"
    media_by_key = {
      str(item.get("media_key")): item
      for item in ((payload.get("includes") or {}).get("media") or [])
      if isinstance(item, dict)
    }
    users_by_id = {
      str(item.get("id")): item
      for item in ((payload.get("includes") or {}).get("users") or [])
      if isinstance(item, dict)
    }
    items = []
    for row in payload.get("data") or []:
      if not isinstance(row, dict):
        continue
      created_at = str(row.get("created_at") or "")
      if created_at:
        try:
          parsed = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
          if parsed < cutoff:
            continue
        except ValueError:
          pass
      media_keys = ((row.get("attachments") or {}).get("media_keys") or [])
      media = next((media_by_key.get(str(key)) for key in media_keys if media_by_key.get(str(key))), {})
      user = users_by_id.get(str(row.get("author_id")), {})
      tweet_id = str(row.get("id") or "")
      username = str(user.get("username") or "")
      url = f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else ""
      items.append(
        {
          "platform": "x",
          "id": tweet_id,
          "title": str(row.get("text") or "X post"),
          "author": username or str(row.get("author_id") or ""),
          "url": url,
          "thumbnail": str(media.get("preview_image_url") or media.get("url") or user.get("profile_image_url") or ""),
          "created_at": created_at,
          "metrics": row.get("public_metrics") or {},
        }
      )
    return items[:limit], None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="Kaspa KAS")
    parser.add_argument("--x-query", default='(Kaspa OR KASPA OR "$KAS") -is:retweet')
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--output", default="state/social-snapshot.json")
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=max(1, args.hours))
    youtube, youtube_error = youtube_rows(args.query, args.limit, cutoff, args.timeout)
    x_items, x_error = x_rows(args.x_query, args.limit, cutoff, args.timeout)
    items = youtube + x_items
    payload = {
      "generated_at": now.isoformat(),
      "window_hours": args.hours,
      "query": args.query,
      "sources": {
        "youtube": {"ok": youtube_error is None, "error": youtube_error or "", "count": len(youtube)},
        "x": {"ok": x_error is None, "error": x_error or "", "count": len(x_items)},
      },
      "items": items,
      "mood_summary": mood_summary(items),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"social_snapshot={output} items={len(payload['items'])}")
    if x_error:
      print(f"x_warning={x_error}", file=sys.stderr)
    if youtube_error:
      print(f"youtube_warning={youtube_error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
