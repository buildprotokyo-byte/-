import os
from flask import Flask, jsonify, render_template, request
import google.generativeai as genai
from flask_cors import CORS

# アプリケーションの初期化
app = Flask(__name__, template_folder='.', static_folder='.')

# CORS（クロスオリジン）を許可する設定
CORS(app)

# ==========================================
# セキュリティ対策およびクラウド移行用設定
# ==========================================

# 1. 社内プロキシ環境下での通信設定 (会社のPCで動かす場合のみ必要)
#    ※注意: 実際のプロキシサーバーのアドレスとポートに書き換えてください
# proxy_url = "http://your_proxy_address:your_proxy_port"
# os.environ['http_proxy'] = proxy_url
# os.environ['https_proxy'] = proxy_url

# 2. APIキーの設定 (Render等のクラウド環境では環境変数から取得)
#    ※注意: AI Studioで取得した 'AIza' から始まる本物のキーを設定してください
#    ローカルで直書きテストする場合: gemini_api_key = "あなたの本物のAPIキー"
#    クラウド環境推奨: os.environ.get("GOOGLE_API_KEY")

gemini_api_key = os.environ.get("GOOGLE_API_KEY")

if not gemini_api_key:
    print("警告: GOOGLE_API_KEYが設定されていません。")

# APIクライアントの構成
genai.configure(api_key=gemini_api_key)

# モデルの定義 (gemini-1.5-flash を使用)
model = genai.GenerativeModel('gemini-1.5-flash')

# ==========================================
# ルート定義 (エンドポイント)
# ==========================================

# 1. ルートページ (index.htmlを表示)
@app.route('/')
def index():
    return render_template('index.html')

# 2. 相談API (POSTリクエストを受け取る)
@app.route('/api/get-advice', methods=['POST', 'OPTIONS'])
def get_advice():
    # ブラウザからのプリフライトリクエスト(OPTIONS)に対する応答
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'})

    try:
        # クライアント(index.html)から送信されたJSONデータを取得
        data = request.get_json()

        if not data:
            return jsonify({'error': 'データが送信されていません'}), 400

        name = data.get('name', '未入力')
        size = data.get('size', '未入力')

        # AIへの指示文 (プロンプト) の作成
        prompt_text = f"案件名:{name}, 規模:{size}万円。この情報に基づき、営業担当者への具体的なアドバイスを3カ条で提案してください。"

        # Geminiモデルにプロンプトを送信し、回答を生成
        response = model.generate_content(prompt_text)

        # 回答テキストを抽出 (安全フィルター等で空の場合はメッセージを返す)
        if response and response.text:
            advice_content = response.text
        else:
            advice_content = "AIが回答を生成できませんでした。入力内容を変えて再度お試しください。"

        # 結果をJSON形式でクライアントに返す
        return jsonify({'advice': advice_content})

    except Exception as e:
        # サーバー側でエラーが発生した場合、詳細をログに出力し、500エラーを返す
        print(f"サーバーエラー発生: {str(e)}")
        return jsonify({'error': 'サーバー内部でエラーが発生しました。'}), 500

# ==========================================
# アプリケーション実行設定
# ==========================================

if __name__ == '__main__':
    # ホストを 127.0.0.1 (ローカルホスト)、ポートを 5000 に固定
    # debug=True は開発時のみ有効化
    app.run(debug=True, host='127.0.0.1', port=5000)

