import streamlit as st
 
from common import setup_page
 
setup_page("我が家の株管理", "📈")
 
st.title("📈 我が家の株管理")
st.caption("家族みんなで使う株主優待・保有株・銘柄解析のダッシュボード")
 
st.markdown("#### メニュー")
st.page_link("pages/1_優待.py", label="優待管理", icon="🎁")
st.caption("株主優待の登録・期限管理（Googleスプレッドシート「優待」シートと同期）")
 
st.divider()
st.caption("🚧 保有株管理・解析（株価監視とLINE通知）のページは今後追加予定です。")
 
