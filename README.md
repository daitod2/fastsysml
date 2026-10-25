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
- `Issue`
  - `severity`、`message`、`line`、`column`、`code`
  - `element`：issue の対象になった要素です。

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
