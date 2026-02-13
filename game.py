"""游戏主模块"""
import random
import logging
from typing import List, Dict, Any, Optional
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import Config
from core.entity import Player, Enemy
from core.item import Item, create_item, Currency
from core.position import Position
from systems.dungeon_generator import DungeonGenerator
from systems.combat_system import CombatSystem
from systems.input_handler import InputHandler, Action
from systems.fov_system import FOVSystem
from systems.trap_system import TrapSystem
from systems.save_system import SaveSystem
from renderer.terminal_renderer import TerminalRenderer


class GameEngine:
    """游戏引擎 - 主游戏循环"""
    
    def __init__(self):
        # 加载配置
        self.config = Config()
        
        # 初始化子系统
        self.dungeon_generator = DungeonGenerator(self.config.map)
        self.combat_system = CombatSystem()
        self.input_handler = InputHandler()
        self.fov_system = FOVSystem(self.config.player.get('vision_range', 8))
        self.trap_system = TrapSystem(self.config.map)
        self.save_system = SaveSystem()
        self.renderer = TerminalRenderer(self.config.responsive)
        
        # 配置日志
        self._setup_logging()
        
        # 游戏状态
        self.game_state = {
            'current_level': 1,
            'map': [],
            'explored': [],
            'visible': [],
            'rooms': [],
            'player': None,
            'enemies': [],
            'items': [],
            'traps': [],
            'messages': [],
            'stairs_up': None,
            'stairs_down': None,
            'width': 80,
            'height': 25
        }
        
        # 游戏控制
        self.running = False
        self.showing_inventory = False
        self.selected_item_index = -1
        self.current_save_slot = 1
    
    def _setup_logging(self):
        """配置日志系统"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('game.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('DungeonHero')
    
    def add_message(self, message: str):
        """添加消息到日志"""
        self.game_state['messages'].append(message)
        # 限制消息数量
        max_messages = self.config.game.get('max_messages', 10)
        if len(self.game_state['messages']) > max_messages:
            self.game_state['messages'] = self.game_state['messages'][-max_messages:]
        
        self.logger.info(message)
    
    def start(self):
        """开始游戏"""
        self.logger.info("游戏开始")
        
        # 创建玩家
        player_config = self.config.player
        self.game_state['player'] = Player(1, 1, player_config)
        
        # 生成第一层地牢
        self._generate_level(1)
        
        # 添加欢迎消息
        self.add_message("欢迎来到地牢，英雄！")
        self.add_message("使用 WASD 或方向键移动，? 查看帮助")
        
        # 开始游戏循环
        self.running = True
        self._game_loop()
    
    def _generate_level(self, level: int):
        """生成新楼层"""
        self.logger.info(f"生成第{level}层地牢")
        
        # 生成地牢
        dungeon_data = self.dungeon_generator.generate(level=level)
        
        # 更新游戏状态
        self.game_state['map'] = dungeon_data['map']
        self.game_state['rooms'] = dungeon_data['rooms']
        self.game_state['width'] = dungeon_data['width']
        self.game_state['height'] = dungeon_data['height']
        self.game_state['stairs_up'] = dungeon_data['stairs_up']
        self.game_state['stairs_down'] = dungeon_data['stairs_down']
        self.game_state['current_level'] = level
        
        # 初始化探索和视野
        self.game_state['explored'] = [[False for _ in range(self.game_state['width'])] 
                                       for _ in range(self.game_state['height'])]
        self.game_state['visible'] = [[False for _ in range(self.game_state['width'])] 
                                      for _ in range(self.game_state['height'])]
        
        # 设置玩家位置
        player_start = dungeon_data['player_start']
        self.game_state['player'].move_to(player_start.x, player_start.y)
        
        # 清空敌人和物品
        self.game_state['enemies'] = []
        self.game_state['items'] = []
        self.game_state['traps'] = []
        
        # 生成敌人
        self._spawn_enemies(level)
        
        # 生成物品
        self._spawn_items(level)
        
        # 生成陷阱
        self.game_state['traps'] = self.trap_system.generate_traps(
            self.game_state['map'],
            self.game_state['width'],
            self.game_state['height'],
            level,
            player_start
        )
        
        # 更新视野
        self._update_fov()
        
        self.add_message(f"进入第{level}层地牢")
    
    def _spawn_enemies(self, level: int):
        """生成敌人"""
        player = self.game_state['player']
        game_map = self.game_state['map']
        rooms = self.game_state['rooms']
        
        # 根据楼层确定敌人数量和类型
        enemy_count = 3 + level * 2
        
        # 获取可用的敌人类型
        available_enemies = []
        for enemy_type, enemy_config in self.config.enemies.items():
            min_level = enemy_config.get('min_level', 1)
            if level >= min_level:
                available_enemies.append((enemy_type, enemy_config))
        
        # Boss楼层
        if level == self.config.game.get('total_levels', 5):
            # 在最后一个房间生成Boss
            boss_room = rooms[-1]
            boss_pos = boss_room.get_random_floor_position()
            boss_config = self.config.get_enemy_config('dragon')
            boss = Enemy(boss_pos.x, boss_pos.y, 'dragon', boss_config)
            self.game_state['enemies'].append(boss)
            self.add_message("警告：你感觉到强大的气息！")
        else:
            # 生成普通敌人
            for _ in range(enemy_count):
                if not available_enemies:
                    break
                
                # 随机选择敌人类型
                enemy_type, enemy_config = random.choice(available_enemies)
                
                # 随机位置（不在玩家附近）
                for _ in range(50):  # 最多尝试50次
                    pos = self.dungeon_generator.get_random_walkable_position(game_map)
                    if pos and pos.distance_to(player.position) > 5:
                        enemy = Enemy(pos.x, pos.y, enemy_type, enemy_config)
                        self.game_state['enemies'].append(enemy)
                        break
    
    def _spawn_items(self, level: int):
        """生成物品"""
        game_map = self.game_state['map']
        items_config = self.config.items
        
        # 生成消耗品
        for _ in range(2 + level):
            item_type = random.choice(['health_potion', 'scroll_fireball', 'scroll_teleport'])
            item_config = items_config['consumables'][item_type]
            item = create_item(item_type, item_config)
            self._place_item(item, game_map)
        
        # 生成装备
        if level > 1:
            for _ in range(1):
                item_type = random.choice(['sword', 'shield', 'axe'])
                item_config = items_config['equipment'][item_type]
                item = create_item(item_type, item_config)
                self._place_item(item, game_map)
        
        # 生成金币
        for _ in range(3 + level):
            item_config = items_config['currency']['gold']
            item = Currency('gold', item_config)
            self._place_item(item, game_map)
    
    def _place_item(self, item: Item, game_map):
        """将物品放置到地图上"""
        for _ in range(50):
            pos = self.dungeon_generator.get_random_walkable_position(game_map)
            if pos:
                item.position = pos
                self.game_state['items'].append(item)
                break
    
    def _update_fov(self):
        """更新视野"""
        player = self.game_state['player']
        game_map = self.game_state['map']
        width = self.game_state['width']
        height = self.game_state['height']
        
        # 计算可见区域
        self.game_state['visible'] = self.fov_system.compute_visible(
            game_map, player.position, width, height
        )
        
        # 更新已探索区域
        self.game_state['explored'] = self.fov_system.update_explored(
            self.game_state['visible'], self.game_state['explored']
        )
    
    def _game_loop(self):
        """游戏主循环"""
        import msvcrt
        
        while self.running:
            # 渲染游戏画面
            if self.showing_inventory:
                self.renderer.render_inventory(self.game_state['player'])
            else:
                self.renderer.render(self.game_state)
            
            # 获取输入
            key = self.renderer.wait_for_key()
            action = self.input_handler.parse_action(key)
            
            if action:
                if self.showing_inventory:
                    self._handle_inventory_action(action)
                else:
                    self._handle_game_action(action)
            
            # 检查游戏结束
            if not self.game_state['player'].is_alive():
                self._game_over()
                return
    
    def _handle_game_action(self, action: Action):
        """处理游戏动作"""
        player = self.game_state['player']
        game_map = self.game_state['map']
        
        # 移动类动作
        if action in [Action.MOVE_UP, Action.MOVE_DOWN, Action.MOVE_LEFT, Action.MOVE_RIGHT,
                     Action.MOVE_UP_LEFT, Action.MOVE_UP_RIGHT, Action.MOVE_DOWN_LEFT, Action.MOVE_DOWN_RIGHT]:
            dx, dy = self.input_handler.get_movement_delta(action)
            new_x = player.position.x + dx
            new_y = player.position.y + dy
            
            # 检查是否有敌人
            target_enemy = self._get_enemy_at(new_x, new_y)
            if target_enemy:
                # 攻击敌人
                result = self.combat_system.attack(player, target_enemy)
                self.add_message(result['message'])
                
                if not target_enemy.is_alive():
                    # 敌人死亡，获得经验和掉落
                    player.gain_xp(target_enemy.xp_reward)
                    self.add_message(f"获得{target_enemy.xp_reward}点经验！")
                    
                    # 检查升级
                    level_up_msg = self.combat_system.check_level_up(player, self.config)
                    if level_up_msg:
                        self.add_message(level_up_msg)
                    
                    # 移除敌人
                    self.game_state['enemies'].remove(target_enemy)
                
                # 敌人回合
                self._enemy_turn()
                self._update_fov()
                return
            
            # 检查是否可以移动
            if self.dungeon_generator.is_walkable(game_map, new_x, new_y):
                player.move_to(new_x, new_y)
                
                # 检查陷阱
                trap_message = self.trap_system.check_trap_at(new_x, new_y, player)
                if trap_message:
                    self.add_message(trap_message)
                    self.trap_system.remove_triggered_traps()
                    
                    # 检查玩家是否死亡
                    if not player.is_alive():
                        self._game_over()
                        return
                
                # 检查是否踩到物品
                self._check_pickup_item(player)
                
                # 检查是否踩到楼梯（自动上下楼）
                tile = game_map[new_y][new_x]
                if tile == '>':
                    self._go_downstairs()
                    return
                elif tile == '<':
                    self._go_upstairs()
                    return
                
                # 敌人回合
                self._enemy_turn()
                self._update_fov()
            else:
                # 检查是否是楼梯（直接踩上去）
                tile = game_map[new_y][new_x]
                if tile == '>':
                    self._go_downstairs()
                elif tile == '<':
                    self._go_upstairs()
        
        # 等待
        elif action == Action.WAIT:
            self.add_message("你等待了一会儿...")
            self._enemy_turn()
            self._update_fov()
        
        # 物品栏
        elif action == Action.INVENTORY:
            self.showing_inventory = True
            self.selected_item_index = -1
        
        # 帮助
        elif action == Action.HELP:
            self.renderer.render_help()
            self.renderer.wait_for_key()
        
        # 存档
        elif action == Action.NUMBER_9:  # 按数字9存档
            if self.save_system.save_game(self.game_state, self.current_save_slot):
                self.add_message(f"游戏已保存到槽位{self.current_save_slot}")
            else:
                self.add_message("保存失败！")
        
        # 退出
        elif action in [Action.QUIT, Action.ESC]:
            self.running = False
            self.add_message("游戏结束")
    
    def _handle_inventory_action(self, action: Action):
        """处理物品栏动作"""
        if self.input_handler.is_number_key(action):
            # 选择物品
            self.selected_item_index = self.input_handler.get_item_index(action)
            self.renderer.render_inventory(self.game_state['player'])
        elif action == Action.USE_ITEM:
            # 使用物品
            if self.selected_item_index >= 0:
                item = self.game_state['player'].get_inventory_item(self.selected_item_index)
                if item:
                    if item.item_category == 'consumable':
                        message = self.combat_system.use_item(
                            self.game_state['player'], item, self.game_state['enemies']
                        )
                        self.game_state['player'].remove_from_inventory(self.selected_item_index)
                        self.add_message(message)
                    elif item.item_category in ['weapon', 'armor']:
                        message = self.combat_system.equip_item(self.game_state['player'], item)
                        self.game_state['player'].remove_from_inventory(self.selected_item_index)
                        self.add_message(message)
                    else:
                        self.add_message(f"无法使用{item.name}")
                else:
                    self.add_message("没有选择物品")
            self.showing_inventory = False
            self.selected_item_index = -1
            self._update_fov()
        elif action == Action.DROP_ITEM:
            # 丢弃物品
            if self.selected_item_index >= 0:
                item = self.game_state['player'].remove_from_inventory(self.selected_item_index)
                if item:
                    item.position = Position(self.game_state['player'].position.x, 
                                           self.game_state['player'].position.y)
                    self.game_state['items'].append(item)
                    self.add_message(f"丢弃了{item.name}")
            self.showing_inventory = False
            self.selected_item_index = -1
            self._update_fov()
        elif action in [Action.QUIT, Action.ESC, Action.INVENTORY]:
            # 关闭物品栏
            self.showing_inventory = False
            self.selected_item_index = -1
    
    def _get_enemy_at(self, x: int, y: int) -> Optional[Enemy]:
        """获取指定位置的敌人"""
        for enemy in self.game_state['enemies']:
            if enemy.is_alive() and enemy.position.x == x and enemy.position.y == y:
                return enemy
        return None
    
    def _check_pickup_item(self, player: Player):
        """检查是否拾取物品"""
        player_pos = player.position
        items_to_remove = []
        
        for item in self.game_state['items']:
            if item.position.x == player_pos.x and item.position.y == player_pos.y:
                if item.item_category == 'currency':
                    # 金币直接拾取
                    player.gold += item.value
                    self.add_message(f"拾取了{item.name}")
                    items_to_remove.append(item)
                elif len(player.inventory) < player.max_inventory:
                    # 添加到物品栏
                    if player.add_to_inventory(item):
                        self.add_message(f"拾取了{item.name}")
                        items_to_remove.append(item)
                else:
                    self.add_message("物品栏已满！")
        
        # 移除已拾取的物品
        for item in items_to_remove:
            self.game_state['items'].remove(item)
    
    def _enemy_turn(self):
        """敌人回合"""
        game_map = self.game_state['map']
        width = self.game_state['width']
        height = self.game_state['height']
        player = self.game_state['player']
        
        enemies_to_remove = []
        
        for enemy in self.game_state['enemies']:
            if enemy.is_alive():
                result = self.combat_system.enemy_take_turn(
                    enemy, player, game_map, width, height
                )
                
                if result['message']:
                    self.add_message(result['message'])
                
                # 检查玩家是否死亡
                if not player.is_alive():
                    return
            else:
                enemies_to_remove.append(enemy)
        
        # 移除死亡敌人
        for enemy in enemies_to_remove:
            if enemy in self.game_state['enemies']:
                self.game_state['enemies'].remove(enemy)
        
        # 更新玩家中毒状态
        poison_message = self.trap_system.update_player_poison(player)
        if poison_message:
            self.add_message(poison_message)
            if not player.is_alive():
                return
        
        # 更新临时状态效果
        temp_effect_message = self._update_temporary_effects(player)
        if temp_effect_message:
            self.add_message(temp_effect_message)
    
    def _update_temporary_effects(self, player) -> Optional[str]:
        """更新临时状态效果"""
        messages = []
        
        # 检查临时攻击力效果
        if hasattr(player, 'temp_atk_duration') and player.temp_atk_duration > 0:
            player.temp_atk_duration -= 1
            if player.temp_atk_duration == 0:
                # 效果结束
                player.atk = max(3, player.atk - 5)  # 移除力量药水的+5攻击力
                messages.append("力量药水的效果消失了。")
        
        return ' '.join(messages) if messages else None
    
    def _go_downstairs(self):
        """下楼梯"""
        total_levels = self.config.game.get('total_levels', 5)
        current_level = self.game_state['current_level']
        
        if current_level >= total_levels:
            # 通关
            self._victory()
        else:
            self._generate_level(current_level + 1)
    
    def _go_upstairs(self):
        """上楼梯"""
        current_level = self.game_state['current_level']
        if current_level > 1:
            self._generate_level(current_level - 1)
        else:
            self.add_message("这是地牢的顶层，无法继续上楼")
    
    def _victory(self):
        """胜利"""
        self.running = False
        self.renderer.render_game_over(self.game_state, is_victory=True)
        self.renderer.wait_for_key()
        self.logger.info("玩家胜利")
    
    def _game_over(self):
        """游戏结束"""
        self.running = False
        self.renderer.render_game_over(self.game_state, is_victory=False)
        self.renderer.wait_for_key()
        self.logger.info("玩家死亡")


def main():
    """主函数"""
    print("正在启动《地牢英雄：终端历险》...")
    print()
    
    game = GameEngine()
    game.start()
    
    print()
    print("感谢游玩！")


if __name__ == "__main__":
    main()