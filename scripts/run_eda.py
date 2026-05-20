# -*- coding: utf-8 -*-
"""验证 EDA notebook 逻辑 + 生成所有图表"""
import pandas as pd, numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt, matplotlib.ticker as mticker, seaborn as sns
import os, warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
sns.set_style('whitegrid')

os.makedirs('data/processed', exist_ok=True)
os.makedirs('data/output', exist_ok=True)

# ==================== 数据加载 ====================
df = pd.read_csv('data/raw/DataCoSupplyChainDataset.csv', encoding='latin-1', low_memory=False)
df['order_datetime'] = pd.to_datetime(df['order date (DateOrders)'])
df['ship_datetime']  = pd.to_datetime(df['shipping date (DateOrders)'])
df['order_date']     = df['order_datetime'].dt.date
df['order_month']    = df['order_datetime'].dt.to_period('M')
df['shipping_delay_days'] = df['Days for shipping (real)'] - df['Days for shipment (scheduled)']

print(f'Rows: {len(df):,} | Orders: {df["Order Id"].nunique():,} | Customers: {df["Customer Id"].nunique():,}')
print(f'Date range: {df["order_datetime"].min()} ~ {df["order_datetime"].max()}')

# ==================== 交付状态 ====================
status = df['Delivery Status'].value_counts()
on_time_rate = (status['Shipping on time'] + status['Advance shipping']) / len(df) * 100
late_rate = status['Late delivery'] / len(df) * 100
print(f'On-time rate: {on_time_rate:.1f}% | Late rate: {late_rate:.1f}%')

print('\n=== Shipping Mode vs Late Rate ===')
print(df.groupby('Shipping Mode')['Late_delivery_risk'].agg(['mean', 'count']).sort_values('mean', ascending=False))

# ==================== 利润 ====================
profit = df[['Order Id', 'Benefit per order']].drop_duplicates()
neg_pct = (profit['Benefit per order'] < 0).mean() * 100
print(f'\nNegative profit: {neg_pct:.1f}% | Median profit: ${profit["Benefit per order"].median():.2f}')

# ==================== 异常检测 ====================
profit_p001 = df['Benefit per order'].quantile(0.005)
df['anom_extreme_loss'] = df['Benefit per order'] < profit_p001
df['anom_ultra_delay'] = (df['shipping_delay_days'] > 3) & (df['Delivery Status'] != 'Shipping canceled')
df['anom_high_margin'] = df['Order Item Profit Ratio'] > 0.45
total_mean, total_std = df['Order Item Total'].mean(), df['Order Item Total'].std()
df['anom_high_value'] = df['Order Item Total'] > (total_mean + 3 * total_std)
df['is_visual_anomaly'] = df['anom_extreme_loss'] | df['anom_ultra_delay'] | df['anom_high_margin'] | df['anom_high_value']

print(f'Anomalies: {df["is_visual_anomaly"].sum():,} / {len(df):,} ({df["is_visual_anomaly"].sum()/len(df)*100:.1f}%)')
print(f'  - Extreme Loss: {df["anom_extreme_loss"].sum():,}')
print(f'  - Ultra Delay: {df["anom_ultra_delay"].sum():,}')
print(f'  - High Margin: {df["anom_high_margin"].sum():,}')
print(f'  - High Value: {df["anom_high_value"].sum():,}')

# ==================== 保存异常样本 ====================
output_cols = [
    'Order Id', 'Order Item Id', 'order date (DateOrders)', 'shipping date (DateOrders)',
    'Category Name', 'Product Name', 'Customer Id', 'Customer Segment',
    'Market', 'Order Region', 'Delivery Status', 'Late_delivery_risk',
    'Days for shipping (real)', 'Days for shipment (scheduled)', 'shipping_delay_days',
    'Sales', 'Benefit per order', 'Order Item Profit Ratio', 'Shipping Mode',
    'anom_extreme_loss', 'anom_ultra_delay', 'anom_high_margin', 'anom_high_value'
]
output_cols = [c for c in output_cols if c in df.columns]
df[df['is_visual_anomaly']][output_cols].to_csv('data/processed/visual_anomalies.csv', index=False)
print(f'Saved {df["is_visual_anomaly"].sum():,} anomalies to data/processed/visual_anomalies.csv')

# ==================== Chart 1: 交付状态 ====================
fig, axes = plt.subplots(1, 3, figsize=(18, 5))
status_counts = df['Delivery Status'].value_counts()
colors_map = {'Late delivery': '#e74c3c', 'Advance shipping': '#2ecc71',
              'Shipping on time': '#3498db', 'Shipping canceled': '#95a5a6'}
wedges, texts, autotexts = axes[0].pie(status_counts.values, labels=status_counts.index,
    autopct='%1.1f%%', colors=[colors_map.get(s, '#999') for s in status_counts.index], startangle=90)
for at in autotexts:
    at.set_fontweight('bold'); at.set_fontsize(12)
axes[0].set_title('Delivery Status Distribution', fontsize=14, fontweight='bold')

delay_sample = df['shipping_delay_days'].sample(20000, random_state=42)
axes[1].hist(delay_sample, bins=60, color='steelblue', edgecolor='white', alpha=0.85)
axes[1].axvline(0, color='red', linestyle='--', linewidth=2, label='On-time (delay=0)')
axes[1].set_xlabel('Actual Days - Scheduled Days', fontsize=12)
axes[1].set_ylabel('Frequency', fontsize=12)
axes[1].set_title('Shipping Delay Distribution', fontsize=14, fontweight='bold')
axes[1].legend(fontsize=10)

groups = [df.loc[df['shipping_delay_days'].abs() < 10, 'shipping_delay_days'][df['Delivery Status'] == s].values
          for s in ['Late delivery', 'Shipping on time', 'Advance shipping']]
bp = axes[2].boxplot(groups, labels=['Late\ndelivery', 'On time', 'Advance'], patch_artist=True)
for patch, color in zip(bp['boxes'], ['#e74c3c', '#3498db', '#2ecc71']):
    patch.set_facecolor(color); patch.set_alpha(0.6)
axes[2].axhline(0, color='black', linestyle='--', linewidth=1, alpha=0.5)
axes[2].set_ylabel('Shipping Delay Days', fontsize=12)
axes[2].set_title('Delay Days by Delivery Status', fontsize=14, fontweight='bold')
plt.tight_layout(); plt.savefig('data/output/eda_delivery_distribution.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 1/6: Delivery Distribution - saved')

# ==================== Chart 2: 运输方式 ====================
fig, axes = plt.subplots(1, 2, figsize=(16, 5.5))
mode_stats = df.groupby('Shipping Mode').agg(
    late_rate=('Late_delivery_risk', 'mean'),
    count=('Order Id', 'nunique')
).sort_values('late_rate', ascending=False)

x = np.arange(len(mode_stats))
width = 0.4
bars1 = axes[0].bar(x - width/2, mode_stats['late_rate']*100, width, color='steelblue', label='Late Rate (%)')
axes[0].set_ylabel('Late Delivery Rate (%)', fontsize=12, color='steelblue')
ax2 = axes[0].twinx()
bars2 = ax2.bar(x + width/2, mode_stats['count'], width, color='lightcoral', alpha=0.6, label='Order Count')
ax2.set_ylabel('Number of Orders', fontsize=12, color='lightcoral')
axes[0].set_xticks(x)
axes[0].set_xticklabels(mode_stats.index, rotation=15, ha='right', fontsize=10)
axes[0].set_title('Shipping Mode: Late Rate vs Order Volume', fontsize=14, fontweight='bold')
axes[0].legend(loc='upper left', fontsize=9)
ax2.legend(loc='upper right', fontsize=9)
for bar, rate in zip(bars1, mode_stats['late_rate']*100):
    axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5, f'{rate:.1f}%',
                 ha='center', va='bottom', fontsize=10, fontweight='bold')

daily_mode = df.groupby(['order_date', 'Shipping Mode'])['Late_delivery_risk'].mean().unstack()
daily_mode_7d = daily_mode.rolling(7, min_periods=1).mean()
daily_mode_7d.plot(ax=axes[1], linewidth=1.5)
axes[1].set_xlabel('Order Date', fontsize=12)
axes[1].set_ylabel('Late Delivery Rate (7d MA)', fontsize=12)
axes[1].set_title('Shipping Mode Late Rate Trend (7-Day Moving Avg)', fontsize=14, fontweight='bold')
axes[1].legend(fontsize=9, loc='upper right')
axes[1].set_ylim(0, 1.05)
plt.tight_layout(); plt.savefig('data/output/eda_shipping_mode.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 2/6: Shipping Mode - saved')

# ==================== Chart 3: 订单利润 ====================
fig, axes = plt.subplots(2, 3, figsize=(18, 10))

profit_data = df[['Order Id', 'Benefit per order']].drop_duplicates()
profit_trim = profit_data['Benefit per order'][(profit_data['Benefit per order'] > -500) & (profit_data['Benefit per order'] < 300)]
axes[0, 0].hist(profit_trim, bins=80, color='steelblue', edgecolor='white', alpha=0.85)
axes[0, 0].axvline(0, color='red', linestyle='--', linewidth=2, label='Break-even')
axes[0, 0].axvline(profit_data['Benefit per order'].median(), color='orange', linestyle='-',
                   linewidth=2, label=f'Median={profit_data["Benefit per order"].median():.0f}')
axes[0, 0].set_xlabel('Profit per Order ($)', fontsize=11)
axes[0, 0].set_ylabel('Frequency', fontsize=11)
axes[0, 0].set_title('Profit per Order Distribution', fontsize=13, fontweight='bold')
axes[0, 0].legend(fontsize=9)

axes[0, 1].boxplot(df['Benefit per order'].values, vert=True, patch_artist=True,
                    boxprops=dict(facecolor='steelblue', alpha=0.6))
axes[0, 1].set_ylabel('Profit per Order ($)', fontsize=11)
axes[0, 1].set_title('Profit per Order -- Box Plot (Outliers Visible)', fontsize=13, fontweight='bold')

sales_trim = df['Sales'][df['Sales'] < 800]
axes[0, 2].hist(sales_trim, bins=60, color='darkseagreen', edgecolor='white', alpha=0.85)
axes[0, 2].axvline(df['Sales'].median(), color='orange', linestyle='-', linewidth=2,
                   label=f'Median={df["Sales"].median():.0f}')
axes[0, 2].set_xlabel('Sales per Item ($)', fontsize=11)
axes[0, 2].set_ylabel('Frequency', fontsize=11)
axes[0, 2].set_title('Sales per Item Distribution', fontsize=13, fontweight='bold')
axes[0, 2].legend(fontsize=9)

daily_profit = df.groupby('order_date')['Benefit per order'].agg(['mean', 'sum']).reset_index()
daily_profit['mean_7d'] = daily_profit['mean'].rolling(7, center=True).mean()
axes[1, 0].plot(pd.to_datetime(daily_profit['order_date']), daily_profit['mean'],
                alpha=0.3, color='steelblue', linewidth=0.5)
axes[1, 0].plot(pd.to_datetime(daily_profit['order_date']), daily_profit['mean_7d'],
                color='darkblue', linewidth=2, label='7d smoothed')
axes[1, 0].axhline(daily_profit['mean'].mean(), color='red', linestyle='--', alpha=0.7,
                   label=f'Overall avg: {daily_profit["mean"].mean():.1f}')
axes[1, 0].set_xlabel('Date', fontsize=11)
axes[1, 0].set_ylabel('Avg Profit per Order ($)', fontsize=11)
axes[1, 0].set_title('Daily Average Profit per Order', fontsize=13, fontweight='bold')
axes[1, 0].legend(fontsize=9)

daily_count = df.groupby('order_date')['Order Id'].nunique()
axes[1, 1].bar(pd.to_datetime(daily_count.index), daily_count.values, color='steelblue', alpha=0.7, width=0.8)
axes[1, 1].axhline(daily_count.mean(), color='red', linestyle='--', linewidth=1.5,
                   label=f'Daily avg: {daily_count.mean():.0f}')
axes[1, 1].set_xlabel('Date', fontsize=11)
axes[1, 1].set_ylabel('Number of Orders', fontsize=11)
axes[1, 1].set_title('Daily Order Count', fontsize=13, fontweight='bold')
axes[1, 1].legend(fontsize=9)

status_counts_order = df['Order Status'].value_counts()
axes[1, 2].barh(range(len(status_counts_order)), status_counts_order.values, color='steelblue', alpha=0.8)
axes[1, 2].set_yticks(range(len(status_counts_order)))
axes[1, 2].set_yticklabels(status_counts_order.index, fontsize=10)
axes[1, 2].set_xlabel('Count', fontsize=11)
axes[1, 2].set_title('Order Status Distribution', fontsize=13, fontweight='bold')
plt.tight_layout(); plt.savefig('data/output/eda_order_profit.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 3/6: Order & Profit - saved')

# ==================== Chart 4: 产品分析 ====================
fig, axes = plt.subplots(2, 2, figsize=(18, 12))

cat_counts = df['Category Name'].value_counts().head(20)
axes[0, 0].barh(range(len(cat_counts)), cat_counts.values[::-1], color='steelblue', alpha=0.8)
axes[0, 0].set_yticks(range(len(cat_counts)))
axes[0, 0].set_yticklabels(cat_counts.index[::-1], fontsize=9)
axes[0, 0].set_xlabel('Count', fontsize=11)
axes[0, 0].set_title('Top 20 Categories by Sales Volume', fontsize=13, fontweight='bold')

cat_late = df.groupby('Category Name').agg(
    late_rate=('Late_delivery_risk', 'mean'),
    count=('Order Id', 'nunique')
).query('count > 200').sort_values('late_rate', ascending=False).head(20)
colors = ['#e74c3c' if rate > 0.55 else '#f39c12' if rate > 0.53 else '#3498db'
          for rate in cat_late['late_rate']]
axes[0, 1].barh(range(len(cat_late)), cat_late['late_rate'].values[::-1]*100, color=colors[::-1], alpha=0.8)
axes[0, 1].set_yticks(range(len(cat_late)))
axes[0, 1].set_yticklabels(cat_late.index[::-1], fontsize=9)
axes[0, 1].set_xlabel('Late Delivery Rate (%)', fontsize=11)
axes[0, 1].axvline(df['Late_delivery_risk'].mean()*100, color='black', linestyle='--', linewidth=1.5,
                   label=f'Global avg: {df["Late_delivery_risk"].mean()*100:.1f}%')
axes[0, 1].set_title('Top 20 Categories by Late Delivery Rate (>200 orders)', fontsize=13, fontweight='bold')
axes[0, 1].legend(fontsize=9)
for i, (rate, cnt) in enumerate(zip(cat_late['late_rate'][::-1], cat_late['count'][::-1])):
    axes[0, 1].text(rate*100 + 0.3, i, f'n={cnt}', va='center', fontsize=8)

prod_sales = df.groupby('Product Name')['Sales'].sum().sort_values(ascending=False).head(15)
axes[1, 0].barh(range(len(prod_sales)), prod_sales.values[::-1], color='darkseagreen', alpha=0.8)
axes[1, 0].set_yticks(range(len(prod_sales)))
axes[1, 0].set_yticklabels([n[:50] for n in prod_sales.index[::-1]], fontsize=8)
axes[1, 0].set_xlabel('Total Sales ($)', fontsize=11)
axes[1, 0].set_title('Top 15 Products by Total Sales', fontsize=13, fontweight='bold')
axes[1, 0].xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))

price_unique = df[['Product Name', 'Product Price']].drop_duplicates()
axes[1, 1].hist(price_unique['Product Price'], bins=30, color='steelblue', edgecolor='white', alpha=0.85)
axes[1, 1].axvline(price_unique['Product Price'].median(), color='orange', linestyle='-', linewidth=2,
                   label=f'Median=${price_unique["Product Price"].median():.0f}')
axes[1, 1].set_xlabel('Product Price ($)', fontsize=11)
axes[1, 1].set_ylabel('Number of Products', fontsize=11)
axes[1, 1].set_title('Product Price Distribution (118 unique SKUs)', fontsize=13, fontweight='bold')
axes[1, 1].legend(fontsize=9)
plt.tight_layout(); plt.savefig('data/output/eda_product_analysis.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 4/6: Product Analysis - saved')

# ==================== Chart 5: 客户分析 ====================
fig, axes = plt.subplots(2, 2, figsize=(18, 11))

market_stats = df.groupby('Market').agg(
    late_rate=('Late_delivery_risk', 'mean'),
    order_count=('Order Id', 'nunique'),
    avg_profit=('Benefit per order', 'mean')
).sort_values('order_count', ascending=False)

x_market = np.arange(len(market_stats))
width_m = 0.35
axes[0, 0].bar(x_market - width_m/2, market_stats['order_count'], width_m, color='steelblue', alpha=0.8, label='Orders')
ax_m = axes[0, 0].twinx()
ax_m.bar(x_market + width_m/2, market_stats['late_rate']*100, width_m, color='#e74c3c', alpha=0.7, label='Late Rate %')
ax_m.axhline(df['Late_delivery_risk'].mean()*100, color='black', linestyle='--', linewidth=1, alpha=0.7)
axes[0, 0].set_xticks(x_market)
axes[0, 0].set_xticklabels(market_stats.index, fontsize=10)
axes[0, 0].set_ylabel('Number of Orders', fontsize=11, color='steelblue')
ax_m.set_ylabel('Late Delivery Rate (%)', fontsize=11, color='#e74c3c')
axes[0, 0].set_title('Market: Order Volume vs Late Rate', fontsize=13, fontweight='bold')
axes[0, 0].legend(loc='upper left', fontsize=9)
ax_m.legend(loc='upper right', fontsize=9)

customer_freq = df.groupby('Customer Id').size()
axes[0, 1].hist(customer_freq[customer_freq < 50], bins=50, color='steelblue', edgecolor='white', alpha=0.85)
axes[0, 1].axvline(customer_freq.median(), color='orange', linestyle='-', linewidth=2,
                   label=f'Median={customer_freq.median():.0f} orders')
axes[0, 1].axvline(customer_freq.mean(), color='red', linestyle='--', linewidth=2,
                   label=f'Mean={customer_freq.mean():.1f} orders')
axes[0, 1].set_xlabel('Number of Orders per Customer', fontsize=11)
axes[0, 1].set_ylabel('Customer Count', fontsize=11)
axes[0, 1].set_title('Customer Purchase Frequency', fontsize=13, fontweight='bold')
axes[0, 1].legend(fontsize=9)

seg_counts = df['Customer Segment'].value_counts()
seg_colors = {'Consumer': '#3498db', 'Corporate': '#e74c3c', 'Home Office': '#2ecc71'}
axes[1, 0].pie(seg_counts.values, labels=seg_counts.index, autopct='%1.1f%%',
              colors=[seg_colors.get(s, '#999') for s in seg_counts.index],
              startangle=90, textprops={'fontsize': 12})
axes[1, 0].set_title('Customer Segment Distribution', fontsize=13, fontweight='bold')

monthly_market = df.groupby(['order_month', 'Market'])['Late_delivery_risk'].mean().unstack()
monthly_market.index = monthly_market.index.astype(str)
monthly_market.plot(ax=axes[1, 1], linewidth=2, marker='o', markersize=3)
axes[1, 1].set_xlabel('Month', fontsize=11)
axes[1, 1].set_ylabel('Late Delivery Rate', fontsize=11)
axes[1, 1].set_title('Monthly Late Rate by Market', fontsize=13, fontweight='bold')
axes[1, 1].legend(fontsize=9, loc='upper right')
axes[1, 1].tick_params(axis='x', rotation=45)
axes[1, 1].set_xticks(range(0, 37, 4))
plt.tight_layout(); plt.savefig('data/output/eda_customer_analysis.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 5/6: Customer Analysis - saved')

# ==================== Chart 6: 异常概览 ====================
fig, axes = plt.subplots(2, 3, figsize=(18, 10))

loss_data = df[df['anom_extreme_loss']]['Benefit per order']
axes[0, 0].hist(df['Benefit per order'][df['Benefit per order'] > -500], bins=100,
                color='lightgray', edgecolor='white', alpha=0.7, label='Normal')
axes[0, 0].hist(loss_data, bins=30, color='#e74c3c', edgecolor='white', alpha=0.9, label=f'Anomaly (n={len(loss_data)})')
axes[0, 0].set_xlabel('Profit per Order ($)', fontsize=10)
axes[0, 0].set_title(f'Extreme Loss Orders (Profit < ${profit_p001:.0f})', fontsize=12, fontweight='bold')
axes[0, 0].legend(fontsize=8)

ultra = df[df['anom_ultra_delay']]
axes[0, 1].hist(df['shipping_delay_days'][df['shipping_delay_days'].between(-2, 8)], bins=50,
                color='lightgray', edgecolor='white', alpha=0.7, label='Normal')
axes[0, 1].hist(ultra['shipping_delay_days'][ultra['shipping_delay_days'] < 15], bins=30,
                color='#e74c3c', edgecolor='white', alpha=0.9, label=f'Anomaly (n={len(ultra):,})')
axes[0, 1].set_xlabel('Shipping Delay Days', fontsize=10)
axes[0, 1].set_title('Ultra-Delayed Orders (delay > 3 days)', fontsize=12, fontweight='bold')
axes[0, 1].legend(fontsize=8)

daily_all = df.groupby('order_date').agg(
    order_count=('Order Id', 'nunique'),
    late_rate=('Late_delivery_risk', 'mean')
).reset_index()
daily_all['is_anom'] = ((daily_all['order_count'] < daily_all['order_count'].quantile(0.05)) |
                         (daily_all['late_rate'] > daily_all['late_rate'].quantile(0.95)))
dates = pd.to_datetime(daily_all['order_date'])
axes[0, 2].scatter(dates[~daily_all['is_anom']], daily_all[~daily_all['is_anom']]['order_count'],
                   s=5, alpha=0.3, color='gray', label='Normal days')
axes[0, 2].scatter(dates[daily_all['is_anom']], daily_all[daily_all['is_anom']]['order_count'],
                   s=20, alpha=0.8, color='#e74c3c', label='Anomalous days')
axes[0, 2].set_xlabel('Date', fontsize=10)
axes[0, 2].set_ylabel('Daily Order Count', fontsize=10)
axes[0, 2].set_title('Anomalous Days (low orders or high late rate)', fontsize=12, fontweight='bold')
axes[0, 2].legend(fontsize=8)

sample = df.sample(15000, random_state=42)
sample_anom = sample[sample['is_visual_anomaly']]
sample_norm = sample[~sample['is_visual_anomaly']]
axes[1, 0].scatter(sample_norm['Order Item Total'],
                   sample_norm['Order Item Profit Ratio'],
                   s=2, alpha=0.2, color='gray', label='Normal')
axes[1, 0].scatter(sample_anom['Order Item Total'],
                   sample_anom['Order Item Profit Ratio'],
                   s=5, alpha=0.5, color='#e74c3c', label='Anomaly')
axes[1, 0].set_xlabel('Order Item Total ($)', fontsize=10)
axes[1, 0].set_ylabel('Profit Ratio', fontsize=10)
axes[1, 0].set_title('Anomalies: Total vs Profit Ratio (sampled)', fontsize=12, fontweight='bold')
axes[1, 0].legend(fontsize=8)

axes[1, 1].scatter(sample_norm['shipping_delay_days'].clip(-2, 10),
                   sample_norm['Benefit per order'].clip(-200, 300),
                   s=3, alpha=0.3, color='gray', label='Normal')
axes[1, 1].scatter(sample_anom['shipping_delay_days'].clip(-2, 10),
                   sample_anom['Benefit per order'].clip(-200, 300),
                   s=15, alpha=0.6, color='#e74c3c', label='Anomaly')
axes[1, 1].set_xlabel('Shipping Delay (days)', fontsize=10)
axes[1, 1].set_ylabel('Profit per Order ($)', fontsize=10)
axes[1, 1].set_title('Anomalies: Delay vs Profit (sampled)', fontsize=12, fontweight='bold')
axes[1, 1].legend(fontsize=8)

anom_types = {
    'Extreme Loss': df['anom_extreme_loss'].sum(),
    'Ultra Delay': df['anom_ultra_delay'].sum(),
    'High Margin': df['anom_high_margin'].sum(),
    'High Value': df['anom_high_value'].sum()
}
axes[1, 2].bar(anom_types.keys(), anom_types.values(),
               color=['#e74c3c', '#f39c12', '#3498db', '#2ecc71'], alpha=0.8)
axes[1, 2].set_ylabel('Count', fontsize=10)
axes[1, 2].set_title('Anomaly Type Breakdown', fontsize=12, fontweight='bold')
for i, (k, v) in enumerate(anom_types.items()):
    axes[1, 2].text(i, v + 500, f'{v:,}', ha='center', fontsize=10, fontweight='bold')
plt.tight_layout(); plt.savefig('data/output/eda_anomalies_overview.png', dpi=150, bbox_inches='tight'); plt.close()
print('Chart 6/6: Anomaly Overview - saved')

# ==================== 最终摘要 ====================
print('\n' + '='*60)
print('EDA 完成 — 关键发现摘要')
print('='*60)
print(f'  准时交付率:      {on_time_rate:.1f}%')
print(f'  延迟交付率:      {late_rate:.1f}%  <-- 核心痛点')
print(f'  亏损订单占比:    {neg_pct:.1f}%')
print(f'  First Class 延迟率: {mode_stats.loc["First Class", "late_rate"]*100:.1f}%  <-- 反直觉发现')
print(f'  异常样本数:      {df["is_visual_anomaly"].sum():,} / {len(df):,} ({(df["is_visual_anomaly"].sum()/len(df)*100):.1f}%)')
print(f'  异常天数:        {daily_all["is_anom"].sum()} / {len(daily_all)}')
print(f'  推荐核心指标:    1) 每日延迟率  2) 每单利润  3) 每日订单量')
print(f'  输出文件:        data/processed/visual_anomalies.csv')
print(f'  图表文件:        data/output/eda_*.png (6 张)')
