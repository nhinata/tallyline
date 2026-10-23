# tallyline

Claude Code の**全セッションのトークン使用量と API 換算の金額を、日・月・年ごとに集計して表示する**、高速なステータスラインです。**使用量が多い理由**も調べられます。

![Max プランでの tallyline の表示](https://raw.githubusercontent.com/nhinata/tallyline/main/assets/screenshot.png)

```
Pro / Max プラン：  [Opus 5.5] │ ctx 120k (12%) │ 5h 23%  7d 41% │ Oct 22.1M tok (≈$14.92)
API 従量課金：      [Opus 5.5] │ ctx 120k (12%)  6.6M tok (≈$3.85) │ Oct 22.1M tok (≈$14.92)
```

作業中に見る値を左に、累計を右端に並べています。このセッション（水色）、アカウント全体（紫）、このマシンの全セッション（黄色）の順です。

```bash
pipx install tallyline && tallyline init    # その後 Claude Code を再起動
```

ステータスライン以外の機能：

- `tallyline diagnose`：使用量が多い原因（時間を空けた後のキャッシュの書き直し、コンテキストに残り続けるツールの出力、長いコンテキスト）と、その対策を表示します。
- `tallyline sessions`：開いている会話の一覧と、キャッシュが切れた会話を再開するときの費用を表示します。

## 特徴

Claude Code 標準のステータスラインに渡されるのは、今のセッションの情報だけです。tallyline はこのマシンの全セッションを記録し続けるので、「今月どれだけ使ったか」をひと目で確認できます。Pro / Max プランの人は、API 換算の金額を見れば、従量課金の API だったらいくらかかっていたかが分かります。

- **速い**：最初の1回以降は、ログに新しく追記された分だけを読みます。表示にかかる時間は約 25ms です。
- **ローカルで完結**：1日1回の更新確認（PyPI に最新版の番号を問い合わせるだけ）を除いて、外部とは一切通信しません。会話の内容や使用量などのデータを送ることはありません。更新確認は `tallyline config update-check off` か、環境変数 `NO_UPDATE_NOTIFIER=1` で止められます。
- **依存なし**：Python 3.9 以上の標準機能だけで動きます。
- **正確**：応答の途中経過として重複して記録された行を除外します。単純に合計すると、使用量が約2倍に膨らみます。

## インストール

```bash
pipx install tallyline      # または: uv tool install tallyline
tallyline init              # ~/.claude/settings.json に statusLine を追加
```

追加したら Claude Code を再起動してください。`init` は実行前に `settings.json.bak` としてバックアップを作ります。すでに別のステータスラインが設定されている場合は置き換えません。置き換えたいときは `--force` を付けてください。

## 表示内容

| 表示 | 範囲 | 意味 |
|---|---|---|
| `[Opus 5.5]` | — | 使用中のモデル |
| `ctx 120k (12%)` | このセッション | 今この瞬間のコンテキスト（会話）の大きさと、上限に対する埋まり具合。`/clear` や `/compact` で下がる。返答のたびにコンテキスト全体が読み直され、キャッシュの期限（何もしないで5分または1時間）が切れたあとの再開では全体が書き直されるため、費用の目安にはトークン数のほうが向いています |
| `6.6M tok (≈$3.85)` | このセッション | API 従量課金のみ：このセッションで使った累計トークン数（サブエージェント分を含む） |
| `5h 23%  7d 41%` | アカウント全体 | Pro / Max プランのみ：5時間・7日間の利用上限に対する使用率（すべての端末・アプリでの利用を含む）。リセットされた枠は `0%` と表示 |
| `Oct 22.1M tok (≈$14.92)` | このマシンの全セッション | 期間（`today`・当月・当年）内の合計 |

`2026*` のように期間の後ろに `*` が付いている場合は、記録がその期間の途中からしかなく、合計が不完全であることを表します。インストール直後はこの状態になるのが普通です。Claude Code は最近のログしか残していないためです（「注意事項」を参照）。

利用上限とセッションの使用量のどちらを出すかは、自動で切り替わります。Claude Code は利用上限の情報を Pro / Max の利用者にだけ渡します。また、定額プランではセッション単位の金額にあまり意味がありません。

Claude Code が利用上限の情報を渡すのは、各セッションで最初の応答を受け取った後です。そのため tallyline は、どのセッションで受け取った値も、最新のものを記録しておきます。これにより、新しいセッションでもすぐに利用上限が表示され、どのセッションでもこのマシンで受け取った最新の値が表示されます。claude.ai や他の端末での利用は次の応答で反映されるので、表示は「少なくともこの値」と考えてください。

Claude は返答のたびにコンテキスト全体を読み直すため、累計トークン数は `ctx` よりずっと速く増えます。その大部分はキャッシュ読み込みです。

トークン数は、入力・出力・キャッシュ作成・キャッシュ読み込みの合計です。キャッシュ読み込みは単価が安いため、どれだけ使い込んでいるかは `≈$` の金額の方が実態に近く表れます。この金額は、定価で計算した API 換算の推定値です。

## 使用量が多い原因を調べる

ステータスラインは「どれだけ使ったか」を表示します。`tallyline diagnose` は「なぜ多いのか」と「何を変えればいいか」を表示します。

```
$ tallyline diagnose
Last 30 days · 9 sessions · 2,087 requests · ≈$331.83 API-equivalent
  cache reads 52%   cache writes 43%   output 6%

Top causes
 1. Resumed after the cache expired                    ≈$122.81   37%
    72 requests after an idle gap wrote the context again (avg 384.0k tokens).
    → Before a long break, /compact or start a new session.
 2. Tool outputs kept in context                       ≈$102.28   31%
    claude-in-chrome 74% · Read 17% · Bash 6% · Edit 1%
    Costliest: Read notes.md (25.1k tokens, carried 482 turns, ≈$2.52)
    → Delegate broad reading or browsing to a subagent so only its summary stays.
 ...
```

| 原因 | 数えているもの |
|---|---|
| Resumed after the cache expired（キャッシュの期限切れ後の再開） | キャッシュの有効期限（5分または1時間）が過ぎてから送ったリクエストで、文脈全体を書き直した費用 |
| Tool outputs kept in context（文脈に残ったツール出力） | ツールの出力が最初に送られてから、セッションの終わりか圧縮（/compact）までの間に、その出力の分としてかかった書き込み・読み込みの費用。出力の大きさは文脈の増え方から求めます |
| Long context（長い文脈） | 各リクエストのキャッシュ読み込みのうち、20万トークンを超えた部分の費用。上の2つと重なります |
| Same file read repeatedly（同じファイルの繰り返し読み込み） | 1つのセッションの中で同じファイルを読み直した分。ツール出力の内訳の一部です |

```bash
tallyline diagnose --days 7          # 直近7日を調べる（初期設定は30日）
tallyline diagnose --session 1fc6    # 1つのセッションの内訳（ID の先頭数文字で指定）
tallyline diagnose --json            # スクリプトや Claude に渡す用
tallyline diagnose --no-titles       # セッション名・プロジェクト・ファイル名を隠す
```

実行したときだけ会話ログを直接読むので、ステータスラインの速さは変わりません。Claude Code は古い会話ログを削除するため（注意事項を参照）、調べられるのは手元に残っている期間だけです。

## 再開すると高くつく会話を見つける

会話をいくつも開いていると、どこで何をしていたか、続きを再開するとどれくらいかかるかが分からなくなりがちです。`tallyline sessions` は、動いている Claude Code の会話を一覧にします。

```
$ tallyline sessions
6 open Claude Code sessions · 4 with an expired cache
Resuming all of them writes 612k tokens again (≈$4.90 API-equivalent).

 #  Last message  Where             State  Context  Cache    Resume  Title
 1  now           tmux work:1 api   busy      142k  warm             Add rate limiting to the API
 2  25m ago       tmux work:2 web   idle       64k  warm             Fix login redirect loop
 3  5h ago        ttys004           idle      231k  expired  ≈$1.85  Migrate billing to the new schema
 4  20h ago       tmux work:3 docs  idle      121k  expired  ≈$0.97  Write the release notes
 5  2.4d ago      ttys002           idle      174k  expired  ≈$1.39  Investigate flaky checkout test
 6  6.1d ago      background        idle       86k  expired  ≈$0.69  Draft the onboarding guide

Resume: an expired session writes its whole context to the cache again on the next message.
Closing a session keeps the conversation; claude --resume picks it up later.
```

| 列 | 意味 |
|---|---|
| Last message | 最後の発言からの経過時間 |
| Where | tmux のセッション・ウィンドウ番号・名前、ターミナル（tty）、またはバックグラウンド（`background`） |
| Context | 今のコンテキストの大きさ。キャッシュが切れている会話では、次の発言でこれがまるごと書き直されます |
| Cache | キャッシュの期限（その会話が直近で使った5分または1時間）が過ぎていなければ `warm` |
| Resume | 次の発言でコンテキストを書き直す費用（API 料金換算の目安） |

表示するだけで、会話を閉じるかどうかは利用者が判断します。会話を閉じても内容は消えず、`claude --resume` で再開できます。バックグラウンドに移した会話は、元の行に `(continued as #n)` と表示されます。

```bash
tallyline sessions --sort resume   # 再開の費用が高い順
tallyline sessions --json          # スクリプト向け
tallyline sessions --no-titles     # タイトル・ディレクトリ・tmux のウィンドウ名を隠す
```

Claude Code が `~/.claude/sessions/` に置いている会話ごとのファイルを読みます。このファイルは公式に説明されていないため、Claude Code の更新で形式が変わる可能性があります。

## 設定

```bash
tallyline config range month   # today / month / year / all
tallyline config cost off      # 金額を非表示にする
tallyline config color off     # 色なしで表示（環境変数 NO_COLOR にも対応）
tallyline config update-check off  # 1日1回の更新確認を止める（環境変数 NO_UPDATE_NOTIFIER=1 でも可）
tallyline config               # 現在の設定を表示
```

設定は `~/.config/tallyline/config.json` に、集計の記録は `~/.cache/tallyline/usage.db` に保存されます。`tallyline rebuild` を実行すると、全ログを読み直します。Claude Code がすでに削除したログの分の記録は、消えずに残ります。

### 単価

単価は、[Anthropic の公式料金ページ](https://platform.claude.com/docs/en/about-claude/pricing)から生成した単価表を、各リリースに同梱しています。GitHub Actions が毎日このページを確認し、料金が変わっていればプルリクエストを自動で作ります。金額は表示のたびに計算するので、単価表が更新されると過去の使用分にも新しい単価が反映されます。

単価表にないモデルの金額は `+?` と表示されます。リリースを待たずに単価を追加したい場合は、`config.json` に書き足してください（単位は 100万トークンあたりの米ドル）。

```json
{
  "prices": {
    "claude-new-model": {
      "input": 3, "cache_write_5m": 3.75, "cache_write_1h": 6,
      "cache_read": 0.3, "output": 15
    }
  }
}
```

Fast モードで処理されたリクエストは、モデルごとの `fast_multiplier` を掛けて計算します。

## 注意事項

- 表示される金額は定価での推定で、実際の請求額ではありません。Pro / Max プランで実際に支払うのはプランの料金です。
- tallyline が数えられるのは、Claude Code がログに書き出した分だけです。裏側で行われるやり取りの一部はログに記録されないため、合計が `/cost` より少なく出ることがあります。自動モードのセッションで比べたところ、`/cost` より約15%少なくなりました（自動モードの許可判定が原因とみられます）。トークン数と金額は同じログから計算しているので、この2つは常に食い違いません。
- Claude Code は古いログを `cleanupPeriodDays`（初期設定は30日）で削除します。tallyline は一度取り込んだ分を保持し続けますが、インストール前に削除されたログの分は集計できません。
- ログファイルは Claude Code の内部形式なので、将来変わる可能性があります。
- 1台のマシンで複数の Claude アカウントを切り替えて使うと、アカウント間で利用上限の記録が混ざることがあります。
- Anthropic の公式ツールではありません。

## 更新

新しい版が出ると、ステータスラインの末尾に `↑ 0.2.0` のように表示されます。次のコマンドで更新してください。

```bash
pipx upgrade tallyline      # または: uv tool upgrade tallyline
```

新しい版には、最新のモデル単価も含まれています。

## アンインストール

```bash
tallyline uninstall && pipx uninstall tallyline
```

## 関連プロジェクト

- [ccusage](https://github.com/ryoppippi/ccusage)：Claude Code をはじめ、多くのコーディングエージェントの使用量を、日・週・月・セッションごとに詳しく集計します。
- [ccstatusline](https://github.com/sirmalloc/ccstatusline)：多くのウィジェットとテーマを備えた、細かく設定できるステータスラインです。

tallyline は、表示を小さく固定したステータスラインに加えて、これらのツールが重点を置いていない2つの問い（使用量がなぜ多かったか、開いている会話のどれが再開すると高くつくか）に絞っています。

## ライセンス

MIT
