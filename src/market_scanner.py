# -*- coding: utf-8 -*-
"""
A股全市场扫描器
===============
职责：
1. 通过AKShare获取全部A股实时行情
2. 自动过滤ST、退市、流动性差个股
3. 统计涨跌家数、板块热度、市场情绪
4. 按价格/市值/涨幅多维度筛选
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import akshare as ak

logger = logging.getLogger(__name__)

# 沪市主板 60xxxx, 科创板 688xxx
# 深市主板 00xxxx, 中小板 002xxx, 创业板 300xxx
# 北交所 8xxxxx
VALID_PREFIXES = ('60', '00', '30', '68')  # 排除北交所、港股通等


class MarketScanner:
    """A股全市场扫描器"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.all_stocks: List[Dict] = []
        self.filtered_stocks: List[Dict] = []
        self.market_overview: Dict = {}
        self.sector_heat: Dict = {}

    def fetch_all_a_shares(self) -> List[Dict]:
        """
        获取全部A股实时行情，排除ST等垃圾股
        返回格式: [{code, name, price, change%, volume, turnover, market_cap, sector}, ...]
        """
        logger.info("正在通过AKShare获取全市场A股数据...")
        try:
            df = ak.stock_zh_a_spot_em()
            logger.info(f"获取到 {len(df)} 只股票原始数据")
        except Exception as e:
            logger.error(f"AKShare获取全市场数据失败: {e}")
            return []

        records = df.to_dict('records')
        all_stocks = []

        for r in records:
            code = str(r.get('代码', ''))
            name = str(r.get('名称', ''))

            # 过滤ST、退市、北交所
            if self._is_invalid_stock(code, name):
                continue

            try:
                price = float(r.get('最新价', 0))
                pct_chg = float(r.get('涨跌幅', 0))
                volume = float(r.get('成交量', 0))  # 手
                amount = float(r.get('成交额', 0))  # 元
                turnover = float(r.get('换手率', 0))
                total_mv = float(r.get('总市值', 0))  # 元
                volume_ratio = float(r.get('量比', 0))

                stock = {
                    'code': code,
                    'name': name,
                    'price': price,
                    'pct_chg': pct_chg,
                    'volume': volume,
                    'amount': amount,
                    'turnover': turnover,
                    'total_mv': total_mv,
                    'volume_ratio': volume_ratio,
                    'sector': self._guess_sector(name, code),
                }
                all_stocks.append(stock)
            except (ValueError, TypeError):
                continue

        self.all_stocks = all_stocks
        logger.info(f"过滤后剩余 {len(all_stocks)} 只正常A股")
        return all_stocks

    def _is_invalid_stock(self, code: str, name: str) -> bool:
        """判断是否为无效股票（ST、退市、北交所）"""
        # ST / *ST / 退市
        if name.startswith('ST') or name.startswith('*ST') or '退' in name:
            return True
        # 北交所 8开头
        if code.startswith('8') or code.startswith('4'):
            return True
        # 只保留主板+创业板
        if not any(code.startswith(p) for p in VALID_PREFIXES):
            return True
        return False

    def _guess_sector(self, name: str, code: str) -> str:
        """根据股票名称和代码粗略判断板块"""
        # 行业关键字匹配（简化版）
        sector_keywords = {
            '银行': ['银行', '601'],
            '证券': ['证券', '券商'],
            '保险': ['保险'],
            '半导体': ['半导体', '芯片', '集成电路', '微', '光电', '华大', '中芯'],
            '医药': ['医药', '药', '医疗', '生物', '健康'],
            '新能源': ['新能源', '光伏', '风电', '锂电', '电池', '宁德'],
            '白酒': ['酒', '茅台', '五粮液', '汾酒', '泸州'],
            '房地产': ['地产', '房地产', '万科', '保利'],
            '电力': ['电力', '能源', '发电', '电网'],
            '煤炭': ['煤炭', '煤业', '焦化'],
            '有色': ['有色', '铜', '铝', '黄金', '钢铁', '矿'],
            '汽车': ['汽车', '车', '比亚迪', '长城', '长安'],
            '家电': ['家电', '海尔', '美的', '格力'],
            '通信': ['通信', '中兴', '烽火'],
            '军工': ['军工', '航天', '航空', '北斗', '国防'],
            'AI/算力': ['算力', 'AI', '人工智能', '大模型', '算法', '寒武纪', '海光'],
        }
        name_lower = name.lower()
        for sector, keywords in sector_keywords.items():
            for kw in keywords:
                if kw.lower() in name_lower or kw in code:
                    return sector
        return '其他'

    def filter_stocks(self,
                      min_price: float = 2.0,
                      max_price: float = 20.0,
                      min_volume_ratio: float = 0.5,
                      min_turnover: float = 0.5) -> List[Dict]:
        """按价格、流动性过滤"""
        filtered = []
        for s in self.all_stocks:
            if s['price'] < min_price or s['price'] > max_price:
                continue
            if s['volume_ratio'] < min_volume_ratio:
                continue
            if s['turnover'] < min_turnover:
                continue
            filtered.append(s)
        self.filtered_stocks = filtered
        logger.info(f"价格{min_price}~{max_price}元过滤后: {len(filtered)}只")
        return filtered

    def compute_market_overview(self) -> Dict:
        """计算市场整体统计"""
        if not self.all_stocks:
            return {}

        total = len(self.all_stocks)
        up = sum(1 for s in self.all_stocks if s['pct_chg'] > 0)
        down = sum(1 for s in self.all_stocks if s['pct_chg'] < 0)
        flat = total - up - down

        limit_up = sum(1 for s in self.all_stocks if s['pct_chg'] >= 9.5)
        limit_down = sum(1 for s in self.all_stocks if s['pct_chg'] <= -9.5)

        total_amount = sum(s['amount'] for s in self.all_stocks if s['amount'])

        avg_pct = sum(s['pct_chg'] for s in self.all_stocks) / max(total, 1)

        self.market_overview = {
            'total_stocks': total,
            'up_count': up,
            'down_count': down,
            'flat_count': flat,
            'limit_up_count': limit_up,
            'limit_down_count': limit_down,
            'up_ratio': round(up / max(total, 1) * 100, 1),
            'total_amount': total_amount,
            'avg_pct_chg': round(avg_pct, 2),
            'timestamp': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M'),
        }
        return self.market_overview

    def compute_sector_heat(self) -> Dict[str, Dict]:
        """按板块统计热度"""
        sector_map = {}
        for s in self.all_stocks:
            sec = s['sector']
            if sec not in sector_map:
                sector_map[sec] = {'count': 0, 'up': 0, 'down': 0, 'avg_pct': 0.0, 'total_amount': 0.0}
            sm = sector_map[sec]
            sm['count'] += 1
            if s['pct_chg'] > 0:
                sm['up'] += 1
            elif s['pct_chg'] < 0:
                sm['down'] += 1
            sm['avg_pct'] += s['pct_chg']
            sm['total_amount'] += s['amount']

        for sec, sm in sector_map.items():
            sm['avg_pct'] = round(sm['avg_pct'] / max(sm['count'], 1), 2)
            sm['total_amount'] = round(sm['total_amount'] / 1e8, 2)  # 转亿元

        # 按平均涨幅排序
        sorted_sectors = dict(
            sorted(sector_map.items(), key=lambda x: x[1]['avg_pct'], reverse=True)
        )
        self.sector_heat = sorted_sectors
        return sorted_sectors

    def pick_candidates(self,
                        top_n: int = 20,
                        min_pct: float = 1.0,
                        max_price: float = 20.0) -> List[Dict]:
        """
        筛选候选股：
        - 今日涨幅 > min_pct
        - 价格 < max_price
        - 换手率 > 1%
        - 排除ST
        - 取涨幅居前的top_n只作为候选
        """
        candidates = [s for s in self.all_stocks
                      if s['pct_chg'] >= min_pct
                      and s['price'] <= max_price
                      and s['turnover'] >= 1.0
                      and s['volume_ratio'] >= 0.8]
        candidates.sort(key=lambda x: x['pct_chg'], reverse=True)
        return candidates[:top_n]

    def run_full_scan(self) -> Dict:
        """执行完整扫描流程"""
        logger.info("===== 全市场扫描开始 =====")

        # 1. 抓取全部A股
        self.fetch_all_a_shares()

        # 2. 市场整体统计
        overview = self.compute_market_overview()
        logger.info(f"涨跌比: {overview.get('up_count',0)}/{overview.get('down_count',0)}")

        # 3. 板块热度
        sectors = self.compute_sector_heat()

        # 4. 候选股（2-20元，涨幅>1%）
        config = self.config
        candidates = self.pick_candidates(
            top_n=config.get('top_n', 15),
            max_price=config.get('max_price', 20),
        )

        logger.info(f"候选股池: {len(candidates)}只")
        logger.info("===== 全市场扫描完成 =====")

        return {
            'overview': overview,
            'sectors': sectors,
            'candidates': candidates,
            'all_stocks': self.all_stocks,
        }


def run_market_scan(config: Optional[Dict] = None) -> Dict:
    """便捷函数：执行全市场扫描"""
    scanner = MarketScanner(config)
    return scanner.run_full_scan()
