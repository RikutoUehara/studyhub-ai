(() => {
  const panel = document.querySelector('[data-ai-usage-url]');
  if (!panel) return;
  const reasons = {
    daily_limit: '本日の上限に達しました。午前0時にリセットされます。',
    emergency_stop: 'AI機能は一時停止中です。残り回数があっても利用できません。',
    budget_stop: '今月のAI利用予算に達したため、この機能は利用できません。',
    busy: 'AI処理が実行中です。完了後にお試しください。',
    configuration_error: 'AI設定を確認中です。時間をおいてお試しください。',
    communication_error: '利用状況を取得できません。通信を確認して「更新」を押してください。',
  };
  let pending;
  function apply(state) {
    window.aiUsageState = state;
    panel.querySelector('[data-usage-message]').textContent = state ? '' : reasons.communication_error;
    panel.querySelector('[data-usage-reset]').textContent = state ?
      '次の切替：' + new Date(state.resets_at).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }) + '（日本時間）' : '';
    panel.querySelectorAll('[data-usage-feature]').forEach(row => {
      const item = state?.features[row.dataset.usageFeature];
      row.querySelector('[data-usage-count]').textContent = item ? `${item.used} / ${item.limit} 回` : '取得できません';
      row.querySelector('[data-usage-remaining]').textContent = item ? `残り ${item.remaining} 回` : '—';
      row.querySelector('[data-usage-reason]').textContent = item?.reason ? reasons[item.reason] : '';
    });
    document.querySelectorAll('[data-ai-feature]').forEach(button => {
      const item = state?.features[button.dataset.aiFeature];
      const blocked = !item?.available;
      if (blocked) {
        if (button.dataset.usageBlocked !== 'true') {
          button.dataset.usageWasDisabled = String(button.disabled);
          button.dataset.usageBlocked = 'true';
        }
        button.disabled = true;
      } else if (!blocked && button.dataset.usageBlocked === 'true') {
        button.disabled = button.dataset.usageWasDisabled === 'true';
        delete button.dataset.usageBlocked;
      }
      button.title = blocked ? reasons[item?.reason || 'communication_error'] : '';
    });
  }
  window.aiUsageReason = feature => {
    const item = window.aiUsageState?.features[feature];
    return item?.available ? null : reasons[item?.reason || 'communication_error'];
  };
  window.refreshAIUsage = () => {
    if (pending) return pending;
    pending = (async () => {
      try {
        const response = await fetch(panel.dataset.aiUsageUrl, { cache: 'no-store' });
        if (response.redirected || !response.ok) throw new Error('Usage unavailable');
        apply(await response.json());
      } catch (_) { apply(null); }
      finally { pending = null; }
    })();
    return pending;
  };
  panel.querySelector('[data-usage-refresh]').addEventListener('click', window.refreshAIUsage);
  window.addEventListener('pageshow', window.refreshAIUsage);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) window.refreshAIUsage(); });
  setInterval(() => { if (!document.hidden) window.refreshAIUsage(); }, 15000);
  apply(null);
  panel.querySelector('[data-usage-message]').textContent = '利用状況を確認しています。';
  window.refreshAIUsage();
})();
