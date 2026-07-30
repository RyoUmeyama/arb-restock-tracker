#!/usr/bin/env python3
"""X(旧Twitter)投稿文の生成モジュール。

検知結果を、Xにそのままコピペできる形に整形する。投稿自体は手動で行うため
X APIは使わない（API費用ゼロ）。将来自動投稿に移す場合も、この
build_post_text() の出力をそのままAPIに渡せる形にしてある。

通知本体（メール/Discord）の文面には手を入れず、末尾に1ブロック足すだけの
独立モジュールとして切ってある（後で別リポジトリへ切り出せるように）。
"""

from .formatter import build_post_text, build_post_block, apply_affiliate_tag

__all__ = ["build_post_text", "build_post_block", "apply_affiliate_tag"]
