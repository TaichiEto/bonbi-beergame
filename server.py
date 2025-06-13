from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
from pathlib import Path
import uuid
import json
import random
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'beer-game-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# 現在のディレクトリを取得
current_dir = Path(__file__).parent.absolute()

# ゲーム状態を保存する辞書
games = {}

# 定数
INVENTORY_COST = 50
BACKLOG_COST = 100
INITIAL_INVENTORY = 12
LEAD_TIME = 2
TOTAL_WEEKS = 52
PLAYER_ROLES = ["retailer", "wholesaler", "distributor", "factory"]
ROLE_NAMES_JP = {
    "retailer": "小売店",
    "wholesaler": "卸売業者", 
    "distributor": "流通業者",
    "factory": "工場"
}

# ディザスターイベント
DISASTER_PROBABILITY = 0.15  # 15%の確率で出現
DISASTER_EVENTS = [
    {"name": "在庫爆発", "description": "在庫コストが2倍に！", "effect": "inventory_cost_double"},
    {"name": "注文混乱", "description": "注文数がランダムに変更される", "effect": "order_chaos"},
    {"name": "配送遅延", "description": "配送パイプラインに遅延発生", "effect": "delivery_delay"},
    {"name": "需要急増", "description": "顧客需要が突然3倍に！", "effect": "demand_surge"},
    {"name": "コスト爆弾", "description": "全員に追加コスト発生", "effect": "cost_bomb"}
]

def create_initial_player_state(name):
    return {
        "uid": None,
        "name": ROLE_NAMES_JP[name],
        "ready": False,
        "inventory": INITIAL_INVENTORY,
        "backlog": 0,
        "cost": 0,
        "incomingOrder": 4,
        "currentOrder": 4,
        "lastShipment": 4,
        "deliveryPipeline": [4] * LEAD_TIME,
        "orderHistory": [],
        "inventoryHistory": [],
        "costHistory": []
    }

def create_new_game():
    return {
        "week": 0,
        "status": "waiting",
        "hostId": None,
        "supplyChainTotalCost": 0,
        "customerDemandHistory": [],
        "disasterEvent": None,
        "soloMode": False,
        "players": {
            role: create_initial_player_state(role) for role in PLAYER_ROLES
        }
    }

@app.route('/')
def index():
    return send_from_directory(current_dir, 'index.html')

@app.route('/<path:filename>')
def serve_file(filename):
    return send_from_directory(current_dir, filename)

@socketio.on('join_game')
def handle_join_game(data):
    game_id = data.get('gameId')
    user_id = data.get('userId')
    
    if not game_id:
        game_id = str(uuid.uuid4())
        games[game_id] = create_new_game()
        games[game_id]['hostId'] = user_id
    
    if game_id not in games:
        games[game_id] = create_new_game()
        games[game_id]['hostId'] = user_id
    
    game = games[game_id]
    
    # 既に参加しているかチェック
    existing_role = None
    for role in PLAYER_ROLES:
        if game['players'][role]['uid'] == user_id:
            existing_role = role
            break
    
    if existing_role:
        player_role = existing_role
    else:
        # 空いている役割を探す
        available_role = None
        for role in PLAYER_ROLES:
            if not game['players'][role]['uid']:
                available_role = role
                break
        
        if available_role:
            player_role = available_role
            game['players'][player_role]['uid'] = user_id
        else:
            emit('game_full')
            return
    
    join_room(game_id)
    emit('joined_game', {
        'gameId': game_id,
        'playerRole': player_role,
        'gameState': game
    })
    
    # 他のプレイヤーにも更新を送信
    socketio.emit('game_update', game, room=game_id)

@socketio.on('start_game')
def handle_start_game(data):
    game_id = data.get('gameId')
    user_id = data.get('userId')
    solo_mode = data.get('soloMode', False)
    
    if game_id not in games:
        return
    
    game = games[game_id]
    if game['hostId'] != user_id:
        return
    
    # ソロモードの場合、AIプレイヤーを追加
    if solo_mode:
        game['soloMode'] = True
        for role in PLAYER_ROLES:
            if not game['players'][role]['uid']:
                game['players'][role]['uid'] = f'ai_{role}'
    else:
        # 最低2人のプレイヤーがいるかチェック
        player_count = sum(1 for role in PLAYER_ROLES if game['players'][role]['uid'])
        if player_count < 2:
            return
    
    game['status'] = 'playing'
    socketio.emit('game_update', game, room=game_id)

@socketio.on('player_ready')
def handle_player_ready(data):
    game_id = data.get('gameId')
    user_id = data.get('userId')
    order_value = data.get('orderValue', 0)
    
    if game_id not in games:
        return
    
    game = games[game_id]
    
    # プレイヤーの役割を見つける
    player_role = None
    for role in PLAYER_ROLES:
        if game['players'][role]['uid'] == user_id:
            player_role = role
            break
    
    if not player_role:
        return
    
    # 注文と準備完了状態を更新
    game['players'][player_role]['currentOrder'] = order_value
    game['players'][player_role]['ready'] = True
    
    # AIプレイヤーの行動をシミュレート
    if game['soloMode']:
        simulate_ai_players(game)
    
    # 全員準備完了かチェック
    active_players = [role for role in PLAYER_ROLES if game['players'][role]['uid']]
    all_ready = all(game['players'][role]['ready'] for role in active_players)
    
    if all_ready:
        # 次の週を計算
        calculate_next_week(game)
    
    socketio.emit('game_update', game, room=game_id)

def simulate_ai_players(game):
    """AIプレイヤーの行動をシミュレート"""
    for role in PLAYER_ROLES:
        player = game['players'][role]
        if player['uid'] and player['uid'].startswith('ai_') and not player['ready']:
            # シンプルなAI戦略: 受信注文数に基づいて発注
            ai_order = max(0, player['incomingOrder'] + random.randint(-2, 3))
            player['currentOrder'] = ai_order
            player['ready'] = True

def apply_disaster_event(game):
    """ディザスターイベントを適用"""
    if random.random() < DISASTER_PROBABILITY:
        event = random.choice(DISASTER_EVENTS)
        game['disasterEvent'] = event
        
        if event['effect'] == 'inventory_cost_double':
            # 在庫コスト2倍は計算時に適用
            pass
        elif event['effect'] == 'order_chaos':
            # 注文数をランダムに変更
            for role in PLAYER_ROLES:
                player = game['players'][role]
                player['currentOrder'] = random.randint(0, 15)
        elif event['effect'] == 'delivery_delay':
            # 配送パイプラインに遅延追加
            for role in PLAYER_ROLES:
                player = game['players'][role]
                if player['deliveryPipeline']:
                    player['deliveryPipeline'].append(0)  # 遅延
        elif event['effect'] == 'demand_surge':
            # 顧客需要3倍
            game['players']['retailer']['incomingOrder'] *= 3
        elif event['effect'] == 'cost_bomb':
            # 全員に追加コスト
            for role in PLAYER_ROLES:
                player = game['players'][role]
                player['cost'] += 500
    else:
        game['disasterEvent'] = None

def calculate_next_week(game):
    game['week'] += 1
    week_supply_chain_cost = 0
    
    # ディザスターイベントチェック
    apply_disaster_event(game)
    
    # Step 1 & 2: Receive & Fulfill Orders
    for role in ['factory', 'distributor', 'wholesaler', 'retailer']:
        p = game['players'][role]
        arrived_shipment = p['deliveryPipeline'].pop(0) if p['deliveryPipeline'] else 0
        p['inventory'] += arrived_shipment
        p['lastShipment'] = arrived_shipment
    
    # Step 3: Fulfill orders
    customer_demand = 8 if game['week'] >= 5 else 4
    
    # ディザスターの需要急増イベント適用
    if game['disasterEvent'] and game['disasterEvent']['effect'] == 'demand_surge':
        customer_demand *= 3
    
    game['customerDemandHistory'].append(customer_demand)
    game['players']['retailer']['incomingOrder'] = customer_demand
    
    for i, role in enumerate(PLAYER_ROLES):
        p = game['players'][role]
        downstream = game['players'][PLAYER_ROLES[i-1]] if i > 0 else None
        
        if downstream:
            p['incomingOrder'] = downstream['currentOrder']
        
        total_demand = p['incomingOrder'] + p['backlog']
        shipment = min(total_demand, p['inventory'])
        
        p['inventory'] -= shipment
        p['backlog'] = total_demand - shipment
        
        upstream = game['players'][PLAYER_ROLES[i+1]] if i < len(PLAYER_ROLES)-1 else None
        if upstream:
            upstream['deliveryPipeline'].append(shipment)
    
    # Step 4: Place orders & Calculate costs
    for role in PLAYER_ROLES:
        p = game['players'][role]
        
        if role == 'factory':
            p['deliveryPipeline'].append(p['currentOrder'])
        
        # ディザスターの在庫コスト2倍イベント適用
        inventory_cost_multiplier = 2 if (game['disasterEvent'] and game['disasterEvent']['effect'] == 'inventory_cost_double') else 1
        inventory_cost = p['inventory'] * INVENTORY_COST * inventory_cost_multiplier
        backlog_cost = p['backlog'] * BACKLOG_COST
        current_week_cost = inventory_cost + backlog_cost
        p['cost'] += current_week_cost
        week_supply_chain_cost += current_week_cost
        
        p['orderHistory'].append(p['currentOrder'])
        p['inventoryHistory'].append(p['inventory'])
        p['costHistory'].append(p['cost'])
        
        p['ready'] = False
    
    game['supplyChainTotalCost'] += week_supply_chain_cost
    
    if game['week'] >= TOTAL_WEEKS:
        game['status'] = 'finished'

@socketio.on('restart_game')
def handle_restart_game(data):
    game_id = data.get('gameId')
    user_id = data.get('userId')
    
    if game_id not in games:
        return
    
    game = games[game_id]
    if game['hostId'] != user_id:
        return
    
    # ゲームをリセット
    games[game_id] = create_new_game()
    games[game_id]['hostId'] = user_id
    
    socketio.emit('game_update', games[game_id], room=game_id)

if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5050)