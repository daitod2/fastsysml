# fastsysml

Fast SysML v2 / KerML parsing and name resolution from Python, using the
[SysML v2 Pilot Implementation](https://github.com/Systems-Modeling/SysML-v2-Pilot-Implementation) (Java) with
performance improvements, in a long-lived JVM. Pure-Python client (Python 3.8+), JDK 21+ required.

```bash
uv add git+https://github.com/daitod2/fastsysml
```

---

SysML v2 Pilot Implementation（Java）を高速化したうえで、Python から使うためのライブラリです。

- **構文解析だけでなく、名前解決と検証まで行えます。** 参照の解決結果（ライブラリの要素への参照も含む）と、構文・リンク・検証のエラーを返します。
- **Python 側は標準ライブラリと `zstandard` だけで動きます。** 常駐する Java プロセスを1つ起動し、標準入出力で JSON を1行ずつやり取りします。JVM の起動とライブラリの読み込みは最初の1回だけで、その後は JIT で最適化された状態で処理されます。
- **pilot の改善版を同梱しています**（`mode="patched"`、既定）。改善の内容と計測の詳細は「[改善の内容](#改善の内容)」を参照してください。

## インストール

```bash
uv add git+https://github.com/daitod2/fastsysml
# または
pip install git+https://github.com/daitod2/fastsysml
```

- **JDK（または JRE）21 以上**が必要です。`java` が PATH にあるか、`JAVA_HOME` または `FASTSYSML_JAVA` を設定してください。
- **初回の実行時に**、pilot の jar（依存ライブラリ込み）と標準ライブラリ（`.index.json` 付き）を取得します。
  - 取得元は conda-forge の公式パッケージ [`jupyter-sysml-kernel-0.62.0`](https://anaconda.org/conda-forge/jupyter-sysml-kernel)（約124 MB）です。
  - 公開されている SHA-256 で内容を確認したうえで、必要なファイルだけをキャッシュに展開します（約140 MB）。
  - キャッシュの場所は `FASTSYSML_HOME` で指定できます。指定しない場合は、Windows では `%LOCALAPPDATA%\fastsysml`、Linux では `~/.cache/fastsysml`、macOS では `~/Library/Caches/fastsysml` です。
  - 前もって取得しておく場合や、ネットワークのない環境で使う場合は、次のようにします。

    ```bash
    python -m fastsysml install                 # 今ダウンロードする
    python -m fastsysml install --from jupyter-sysml-kernel-0.62.0-pyhd8ed1ab_0.conda   # 手元のファイルから
    python -m fastsysml info                    # キャッシュの場所と状態
    ```

## 使い方

```python
from fastsysml import SysMLParser, External, Unresolved

with SysMLParser() as parser:  # Java プロセスを起動する（約1秒）
    # 構文解析のみ（ライブラリは使わない）
    model = parser.parse("package P { part def A; part a : A; }")
    for part in model.find(type="PartUsage"):
        print(part.name, part.line, part.text)

    # 名前解決と検証まで
    model = parser.parse_file("Vehicle.sysml", resolve="lazy", validate=True,
                              derived=["qualifiedName"])
    for issue in model.errors:
        print(issue.line, issue.column, issue.message)
    typing = model.find(type="FeatureTyping")[0]
    print(typing.ref("type"))  # Element（同じモデル内） / External（ライブラリなど） / Unresolved

    # 互いに参照し合う複数のファイル
    models = parser.parse_files(["A.sysml", "B.sysml"], resolve="lazy", validate=True)
```

コマンドラインからも使えます。

```bash
python -m fastsysml parse Vehicle.sysml --resolve lazy --validate
```

### オプション

- `resolve`
  - `"none"`（既定）：構文解析のみです。
  - `"lazy"`：モデルの参照を解決します。リンクに必要な参照だけを解決する方式です。
  - `"all"`：参照の解決に加えて、すべての派生プロパティを評価します（`EcoreUtil.resolveAll`。pilot のツールと同じです）。

  `lazy` と `all` は、参照の解決結果と issue が SysML v2 Release の全310ファイルで一致しました。`lazy` のほうが4倍以上速いので、通常は `lazy` を使ってください。
- `validate=True`：検証も行います。構文エラー・リンクエラー・検証エラーが `model.issues` に入ります。`validate=False` の場合は、構文エラーとリンクエラーだけが入ります。
- `derived=[...]`：要素ごとに、指定した派生プロパティ（`qualifiedName`、`type`、`featuringType` など）の値も返します。
- `positions=False`：位置の情報（行番号やオフセット）を省きます。
- `keep=True`：入力を読み込んだままにして、あとの呼び出しから参照できるようにします。`unload()` を呼ぶまで残ります。
- `uris=True`：要素ごとに URI（`Element.uri`）も返します。ほかのモデルからの参照（`External.uri`）と突き合わせるときに使います。
- `SysMLParser(mode=...)`
  - `"patched"`（既定）：pilot の改善版です。
  - `"userside"`：pilot の jar はそのままで、拡張点を使った改善だけを入れたものです。
  - `"plain"`：pilot をそのまま使います。
- `SysMLParser(preload=True)`：ライブラリを起動時に読み込みます。指定しない場合は、最初に名前解決するときに読み込みます（約2秒）。
- `SysMLParser(preresolve=True, tables=True)`：ライブラリを起動時にすべて解決して、名前表の遅延生成を有効にします。起動に1分以上かかります。
- `SysMLParser(jvm_args=[...])`：JVM のオプションです。既定は `-Xss8m -Xmx4g` です。

### モデル

- `Model`
  - `elements`：すべての要素です。`id` で引けます。
  - `roots`
  - `issues`、`errors`
  - `find(type=..., name=...)`
  - `timing`：Java 側での処理時間です（ミリ秒）。
- `Element`
  - `type`：メタクラス名です（例：`"PartUsage"`）。
  - `name`（宣言名）、`short_name`
  - `owner`、`role`（所有している側のフィーチャー名）、`children`、`walk()`
  - `attrs`：派生でない属性です。
  - `refs`、`ref(name)`：派生でない参照です。値は `Element`、`External`（`qualified_name`、`uri`）、`Unresolved`（`text`）のどれかです。
  - `derived`：`derived=[...]` で指定した派生プロパティです。
  - `line`、`end_line`、`offset`、`length`、`text`
  - `uri`：`uris=True` のときだけ入ります。
- `Issue`
  - `severity`、`message`、`line`、`column`、`code`
  - `element`：issue の対象になった要素です。

### URI で要素を取得する

```python
model = parser.parse("part def A { attribute x : ScalarValues::Real; }", resolve="lazy")
real = model.find(type="FeatureTyping")[0].ref("type")          # External
[library] = parser.get([real.uri], subtree=True, derived=["qualifiedName"])
print(library.roots[0].type, library.owner)                     # DataType External('ScalarValues')
```

- `parser.get(uris, subtree=False, derived=[...])`：URI（`External.uri` や `Element.uri`）で要素を取得します。ライブラリの要素や、`keep=True` で読み込んだままの入力の要素が対象です。
  - 要素ごとに、その要素を根とする `Model` を返します。見つからないときは `None` です。
  - `subtree=True` のときは、その要素が所有する要素もすべて含めます。
  - 所有者は `Model.owner`（`External`）に入ります。
- `parser.metaclasses()`：メタクラスとその上位のメタクラスの一覧です。

## モデルを見て回る（`fastsysml.browser`）

抽象構文のままだと、`part a : A;` は「`PartUsage` の下にある `FeatureTyping` 要素の `type` が `A`」と表され、関係そのものが要素になっています。`fastsysml.browser` はこれを、モデルを作る人が見る形に組み直します。

- **ノード**は、パーツや定義、ポート、要求といった要素です。メンバーシップは飛ばして、名前空間の下に直接並べます。
- **辺**は、要素の間の関係です。関係の要素から作ります。
- **構造だけを表す要素は隠します。** 特化、フィーチャーの連鎖、式、多重度、ドキュメント、匿名の接続の端などです。これらの情報は辺やノードの属性に移します。

### 画面で見る

```bash
python -m fastsysml browse Vehicle.sysml          # 複数のファイルも指定できる
```

ファイルを読み込むと（名前解決と検証まで行います）、ローカルのサーバーが `http://127.0.0.1:8765/` で起動し、ブラウザが開きます。外部のライブラリは使っていません。

- **上部**：検索ボックス（書き方は「書き方」ボタンで表示）、戻る・進む、指摘の数、再読み込み
- **左**
  - 「種類」：メタクラスごとの件数。クリックすると、そのメタクラスの一覧を出します。
  - 「ツリー」：所有の階層。選んだ要素の位置まで開きます。
- **中央**：一覧の表
  - 列は、結果に値があるものだけを出します（型、特化元、再定義、使用数、多重度、値など）。
  - 見出しのクリックで並べ替え、入力欄で結果の絞り込み、CSV への書き出しができます。
  - ↑↓ キーで選択を移せます。
- **右**：要素の詳細
  - 所有者のパンくず、属性、ドキュメント、出ていく関係と入ってくる関係（種類ごと）、所有する要素、ソース、指摘を表示します。
  - 関係の相手をクリックすると、その要素に移ります。
  - 入ってくる関係の「一覧に出す」で、その関係を持つ要素を中央の一覧に出します（例：ある定義の使用箇所）。
  - ライブラリの要素は、開いたときにブリッジから取得します。
- **URL**：選んだ要素と検索条件は URL に入るので、ブラウザの戻る・進む、リンクの共有、ブックマークができます。

オプションは、`--port`（0 なら空いているポート）、`--no-open`（ブラウザを開かない）、`--no-validate`、`--mode` です。Python から使うときは、`fastsysml.browser.server.serve(workspace)` を呼びます。

### Python から使う

```python
from fastsysml.browser import Workspace

with Workspace() as ws:            # SysMLParser を起動する（ws = Workspace(parser) で既存のものも使える）
    ws.load(["Vehicle.sysml"])     # 名前解決と検証まで行う。複数のファイルを互いに参照させられる
    [wheel] = ws.search("type:PartDefinition name:Wheel")
    for usage in ws.usages(wheel):                 # Wheel で型付けされた要素
        print(usage.qualified_name, usage.multiplicity)
    for edge in wheel.into:                        # Wheel に入ってくる関係
        print(edge.kind, edge.source.label)
    [engine] = ws.search("type:PartUsage name:engine in:PartsTree::vehicle_b")
    for edge in engine.out:                        # 例：perform、satisfy、typing
        print(edge.kind, edge.target_label)
    [mass] = ws.search("type:AttributeUsage name:mass in:Vehicle")
    quantity = mass.targets("subsetting")[0]       # ライブラリの要素（ISQBase::mass）は、最初は名前だけの stub
    ws.node(quantity)                              # 必要になったときにブリッジから取得する
    nodes, edges = ws.neighborhood(wheel, depth=2) # 周辺の要素と辺
    ws.sql("SELECT kind, count(*) FROM edges GROUP BY kind")
```

- `Node`
  - `label`、`name`（実効名）、`qualified_name`、`metaclass`
  - `owner`、`children`、`membership`（どのメンバーシップで所有されているか）
  - `attributes`、`doc`、`metadata`、`value`（`= ...` の式のテキスト）、`multiplicity`
  - `out`、`into`、`targets(kind)`、`sources(kind)`
  - `line`、`text`、`element`（元の `Element`）
  - `stub`：参照されているだけで、まだ読み込まれていないことを表します。
- `Edge`
  - `kind`、`source`、`target`
  - `via`：辺の元になった要素のキーです。
  - `path`、`source_path`：フィーチャーの連鎖（例：`rearAxle.leftHalfAxle`）を表します。
  - `text`：解決できなかった参照の、ソース中の書き方です。
- 辺の種類
  - 特化：`typing`、`subclassification`、`redefinition`、`subsetting`、`reference`（`::>`）、`conjugation`
  - 接続：`connection`、`interface`、`flow`、`allocation`、`binding`、`succession`（端の参照先どうしを結びます）。接続や遷移から各端への辺は `end` です。
  - 振る舞い：`transition`（遷移元の状態から遷移先の状態へ）、`perform`、`exhibit`、`include`
  - 要求：`satisfy`（`by` の要素から要求へ）、`verify`（検証ケースから要求へ）、`assert`
  - その他：`dependency`、`import`、`membership`（別名など）、`annotation`、`value`（値が参照だけの場合）
  - ほかのメタクラスの関係は、そのメタクラスの名前になります（例：`disjoining`）。
- 検索（`ws.search(query)`）
  - 語を並べると、すべての語を含むノードが返ります。対象は名前・修飾名・ドキュメント・メタデータ・値で、大文字と小文字は区別しません。`*` はワイルドカードです。
  - 欄を指定することもできます。
    - `type:`：そのメタクラスとサブクラス
    - `metaclass:`：そのメタクラスだけ
    - `name:`
    - `in:`：修飾名で指定した名前空間の中
    - `meta:`：メタデータ
    - `file:`
    - 辺の種類（例：`typing:P::Engine`）：その修飾名（または名前）の要素への辺を持つもの。この例では、P::Engine の使用箇所になります。
  - 結果は名前がよく一致するものから並びます。
  - 標準ライブラリの SQLite（`:memory:`）を使っています。trigram トークナイザー（SQLite 3.34 以上）があれば全文索引を使い、なければ LIKE で探します。
- 同じ種類の一覧と、たどる操作
  - `ws.of_type("PartUsage")`
  - `ws.usages(definition, specializations=False)`
  - `ws.specializations(node)`、`ws.generals(node)`
  - `ws.neighborhood(node, depth, kinds, direction, ownership)`
- `ws.sql(query)`：`nodes`、`edges`、`metaclasses` の表に SQL を直接実行します。列は `fastsysml/browser/search.py` を参照してください。
- `ws.reload()`：ファイルが変わったときに読み込み直します。

制限：前の要素から暗黙に続く succession（`then b;` だけの書き方）は、最初の端が推論で決まるため、辺になりません。send や accept の宛先・経由（`to`、`via`）も、まだ辺にしていません。

## 速さ

計測は2026-10-25、Java 25.0.1、Windows で行いました。対象は [SysML v2 Release](https://github.com/Systems-Modeling/SysML-v2-Release)（2026-08）です。`bench.py` で再現できます。

- **構文解析のみ**（全404ファイル、46,454行、1回の呼び出しで1ファイル）：JIT 最適化後で **3.6 秒**（約111ファイル/秒、約12,800行/秒）でした。このうち Java 側の構文解析は1.5秒で、残りは JSON の受け渡しと Python 側の処理です。
- **名前解決と検証**（`SimpleVehicleModel.sysml`、8,213要素、2回目、Python 側の処理を含む全体の時間）：

| | `resolve="lazy"` | `resolve="all"` |
|---|---|---|
| `mode="patched"`（既定） | **2.2 秒** | 14.7 秒 |
| `mode="plain"`（pilot をそのまま） | 2.9 秒 | 25.9 秒 |

- **コーパス全体の名前解決と検証**（`sysml/src` と `kerml/src` の310ファイル、1回の呼び出しで1ファイル）：`lazy` で15.1秒、`all` で67.8秒でした。

## 改善の内容

`mode="patched"` には、pilot（0.62.0 = タグ `2026-08`）に次の改善を加えた版を使っています。ソースとパッチは `java/` にあります。

- 派生プロパティが同じ値を何度も計算し直さないようにしました。入れ子になった派生リストの再評価、アダプター検索、stream の固定費をなくしています（`java/patches/pilot-0.62.0-resolve-perf.patch`）。
- 名前解決をメモ化しました。また、名前空間ごとの名前表を遅延生成し、それを使って探索を枝刈りします（`java/patches/pilot-0.62.0-scope-memo.patch`）。

改善前後で結果が変わらないことは、名前解決と検証を終えたモデルの「指紋」で確認しています。指紋は、全要素について、すべての参照（派生も含む）、すべての属性、検証結果を書き出して比べるものです。SysML v2 Release の全310ファイルで一致しました。
名前解決（`EcoreUtil.resolveAll`）にかかる時間は、`SimpleVehicleModel.sysml` で 92.6 秒 → 24.0 秒（パッチのみ）→ 8〜10 秒（`.index.json` とメモ化を加えた場合）になりました。

## ソースからのビルド

同梱しているクラスファイルは `--release 21` でコンパイルしたものです。

```bash
sh scripts/build_java.sh                         # ブリッジと userside（java/bridge、java/userside）
sh scripts/build_patched.sh <SysML-v2-Pilot-Implementation のクローン（タグ 2026-08）>   # patched
```

## テスト

```bash
uv run python -m unittest discover -s tests
uv run python bench.py parse     # SYSML_V2_RELEASE に SysML-v2-Release のクローンを指定
```

## ライセンス

- このリポジトリは [Eclipse Public License 2.0](LICENSE) で公開しています。
- `java/patches` と、そこからコンパイルした `src/fastsysml/java/patched` は、SysML v2 Pilot Implementation（EPL-2.0、© 各著作権者）のソースに変更を加えたものです。
- 初回の実行時に取得する jar と標準ライブラリは、conda-forge が配布する Jupyter SysML カーネルのパッケージに含まれているものです。このリポジトリには含めていません。jar に含まれる各コンポーネントは、それぞれのライセンス（EPL-2.0、Apache-2.0 など）に従います。
- このプロジェクトは OMG や Systems Modeling Community とは関係のない、非公式のものです。
