/* 行程结果渲染器：供 plan.html / result.html 共享 */

const activityIcons = {
    hotel: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/><path d="M9 16v-6"/><path d="M15 16v-6"/></svg>`,
    restaurant: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2v0a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"/></svg>`,
    attraction: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 18l4-8 4 8"/><path d="M12 10V4"/><path d="M4 22h16"/></svg>`,
    transport: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M6 18v2"/><path d="M18 18v2"/><path d="M6 10h12"/></svg>`,
    activity: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>`
};

function renderResult(data) {
    const sec = document.getElementById('resultSection');
    if (!sec) return;
    sec.style.display = 'block';

    const it = data.itinerary || {};
    const days = it.days || [];
    const risk = data.risk || {};
    const agents = data.agent_results || {};

    let html = `
    <div class="result-header animate-fade-up">
        <div>
            <div class="heading-md">${it.title || '行程规划结果'}</div>
            <div class="text-muted" style="font-size:0.85rem;margin-top:4px;">${data.destination} · ${data.days}天 · ${data.travelers}人</div>
        </div>
        <div class="result-meta">
            <span class="tag ${data.llm_used ? 'tag-accent' : 'tag-muted'}">${data.llm_used ? 'LLM 增强' : '模板生成'}</span>
            ${data.total_duration_ms ? `<span class="tag tag-muted">${data.total_duration_ms}ms</span>` : ''}
        </div>
    </div>

    <div class="card overview-card animate-fade-up animate-delay-1">
        <div class="card-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                <line x1="16" y1="2" x2="16" y2="6"/>
                <line x1="8" y1="2" x2="8" y2="6"/>
                <line x1="3" y1="10" x2="21" y2="10"/>
            </svg>
            行程概览
        </div>
        <div class="overview-grid">
            <div class="overview-item">
                <div class="overview-value">${data.destination}</div>
                <div class="overview-label">目的地</div>
            </div>
            <div class="overview-item">
                <div class="overview-value">${data.days}天</div>
                <div class="overview-label">行程</div>
            </div>
            <div class="overview-item">
                <div class="overview-value">${data.travelers}人</div>
                <div class="overview-label">出行</div>
            </div>
            <div class="overview-item">
                <div class="overview-value">${data.budget ? '¥' + data.budget : '-'}</div>
                <div class="overview-label">预算/人</div>
            </div>
        </div>
        ${it.summary ? `<div class="divider"></div><p style="color:var(--text-secondary);">${it.summary}</p>` : ''}
        ${it.hotel ? `<div class="mt-2" style="font-size:0.9rem;"><strong style="color:var(--text);">推荐酒店：</strong><span style="color:var(--text-secondary);">${it.hotel}</span></div>` : ''}
        ${it.transport ? `<div style="font-size:0.9rem;"><strong style="color:var(--text);">交通方案：</strong><span style="color:var(--text-secondary);">${it.transport}</span></div>` : ''}
    </div>`;

    if (data.planning_trace && data.planning_trace.length) {
        html += renderPlanningTrace(data.planning_trace);
    }

    if (agents.weather_result) {
        html += renderWeatherCard(agents.weather_result);
    }

    html += `
    <div class="animate-fade-up animate-delay-2">
        <div class="heading-sm mb-2">每日行程</div>`;

    days.forEach(day => {
        html += `
        <div class="card day-card">
            <div class="day-header">
                <div class="day-header-left">
                    <div class="day-number">${day.day}</div>
                    <div>
                        <div class="day-theme">${day.theme || '第' + day.day + '天行程'}</div>
                        ${day.weather ? `<div class="day-weather">${day.weather}</div>` : ''}
                    </div>
                </div>
            </div>
            <div class="activity-list">`;

        (day.activities || []).forEach(act => {
            html += `
            <div class="activity-item">
                <div class="activity-icon">${activityIcons[act.type] || activityIcons.activity}</div>
                <div class="activity-body">
                    <div class="activity-name">${act.name}</div>
                    ${act.note ? `<div class="activity-note">${act.note}</div>` : ''}
                </div>
                <div class="activity-time">${act.time || ''}</div>
            </div>`;
        });

        html += `</div></div>`;
    });

    html += `</div>`;

    if (risk.data && risk.data.warnings && risk.data.warnings.length > 0) {
        html += `
    <div class="card risk-card animate-fade-up animate-delay-2">
        <div class="card-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            出行提醒
        </div>`;
        risk.data.warnings.forEach(w => {
            const tagClass = {high: 'tag-danger', medium: 'tag-warning', low: 'tag-info'}[w.level] || 'tag-warning';
            html += `
            <div class="risk-item">
                <div class="risk-header">
                    <span class="tag ${tagClass}">${w.type}</span>
                    <span class="risk-message">${w.message}</span>
                </div>
                <div class="risk-suggestion">建议：${w.suggestion}</div>
            </div>`;
        });
        html += `</div>`;
    }

    html += `
    <div class="animate-fade-up animate-delay-3">
        <div class="heading-sm mb-2">Agent 执行详情
            <span class="text-muted" style="font-size:0.75rem;font-weight:400;">（点击卡片展开内嵌详情页）</span>
        </div>
        <div class="agent-grid">${Object.entries(agents).map(([key, a]) => renderAgentCard(key, a)).join('')}</div>
    </div>

    <div class="text-center mt-3 animate-fade-up animate-delay-3">
        <button class="btn btn-primary" onclick="location.reload()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
            重新规划
        </button>
    </div>`;

    sec.innerHTML = html;
}

function escapeHtml(text) {
    if (text == null) return '';
    return String(text).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function toggleAgentDetail(key) {
    const detail = document.getElementById('agent-detail-' + key);
    const chevron = document.getElementById('agent-chevron-' + key);
    if (!detail) return;
    if (detail.style.display === 'none' || detail.style.display === '') {
        detail.style.display = 'block';
        if (chevron) chevron.classList.add('expanded');
    } else {
        detail.style.display = 'none';
        if (chevron) chevron.classList.remove('expanded');
    }
}

function togglePlanningTrace() {
    const timeline = document.getElementById('planning-trace-timeline');
    const chevron = document.getElementById('planning-trace-chevron');
    if (!timeline) return;
    if (timeline.style.display === 'none' || timeline.style.display === '') {
        timeline.style.display = 'flex';
        if (chevron) chevron.classList.add('expanded');
    } else {
        timeline.style.display = 'none';
        if (chevron) chevron.classList.remove('expanded');
    }
}

function formatTraceContent(step) {
    if (step.content) return step.content;
    if (step.type === 'observation' && step.result) {
        const r = step.result;
        const summary = r.summary || {};
        const agent = r.agent_name || step.tool || '';
        if (step.tool === 'search_hotels') return `${agent} 返回 ${summary.hotel_count ?? 0} 家酒店候选`;
        if (step.tool === 'search_restaurants') return `${agent} 返回 ${summary.restaurant_count ?? 0} 家餐厅候选`;
        if (step.tool === 'search_attractions') return `${agent} 返回 ${summary.attraction_count ?? 0} 个景点候选`;
        if (step.tool === 'get_weather') return `${agent} 返回当前天气与未来 ${summary.forecast_days ?? 0} 天预报`;
        if (step.tool === 'plan_transport') return `${agent} 返回 ${summary.options ?? 0} 种交通方案`;
        if (step.tool === 'risk_check') return `${agent} 识别 ${summary.warning_count ?? 0} 条风险`;
        return `${agent} 完成，耗时 ${r.duration_ms || 0}ms`;
    }
    if (step.tool_input && Object.keys(step.tool_input).length) {
        return '参数：' + JSON.stringify(step.tool_input);
    }
    return '';
}

function agentSummaryText(key, data) {
    if (key === 'hotel_result') return `找到 ${(data.hotels || []).length} 家酒店候选`;
    if (key === 'restaurant_result') return `找到 ${(data.restaurants || []).length} 家餐厅候选`;
    if (key === 'attraction_result') return `找到 ${(data.attractions || []).length} 个景点候选`;
    if (key === 'weather_result') return `当前 ${(data.current || {}).temp || '?'}°C ${(data.current || {}).description || ''}`;
    if (key === 'transport_result') return `距离 ${data.distance_km != null ? data.distance_km + ' km' : '-'}，${(data.options || []).length} 种交通方案`;
    if (key === 'risk_result') return `${(data.warnings || []).length} 条出行提醒`;
    return '';
}

function renderAgentCard(key, a) {
    const data = a.data || {};
    let summary = '';
    if (a.error) {
        summary = '';
    } else if (a.reasoning) {
        summary = escapeHtml(a.reasoning.substring(0, 70)) + (a.reasoning.length > 70 ? '...' : '');
    } else {
        summary = agentSummaryText(key, data);
    }
    const chevron = `<svg class="agent-chevron" id="agent-chevron-${key}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"/>
    </svg>`;
    return `
    <div class="agent-detail-card">
        <div class="agent-detail-header" onclick="toggleAgentDetail('${key}')">
            <div>
                <div class="agent-detail-name">${escapeHtml(a.agent_name)}</div>
                <div class="agent-detail-meta">${a.duration_ms || 0}ms · ${a.status === 'completed' ? '完成' : '失败'}</div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span class="tag ${a.status === 'completed' ? 'tag-success' : 'tag-danger'}">${a.status === 'completed' ? '完成' : '失败'}</span>
                ${chevron}
            </div>
        </div>
        ${a.error ? `<div class="agent-summary" style="color:var(--danger);">${escapeHtml(a.error)}</div>` : ''}
        ${summary && !a.error ? `<div class="agent-summary">${summary}</div>` : ''}
        <div class="agent-embedded-page" id="agent-detail-${key}">
            ${renderAgentEmbedded(key, a)}
        </div>
    </div>`;
}

function renderAgentEmbedded(key, a) {
    let data = a.data || {};
    // 兼容旧数据：如果 data 里只有 raw 或嵌套 data，做兜底处理
    if (data.raw != null) {
        return renderRawDetail(data.raw, '该条 Agent 数据在保存时被截断或解析失败，以下是原始内容：');
    }
    if (data.data) {
        data = data.data;
    }
    if (key === 'attraction_result') return renderAttractionDetail(data);
    if (key === 'hotel_result') return renderHotelDetail(data);
    if (key === 'restaurant_result') return renderRestaurantDetail(data);
    if (key === 'weather_result') return renderWeatherDetail(data);
    if (key === 'transport_result') return renderTransportDetail(data);
    if (key === 'risk_result') return renderRiskDetail(data);
    return `<div class="text-muted" style="font-size:0.8rem;">暂无详情</div>`;
}

function renderRawDetail(raw, message) {
    const text = typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2);
    return `<div class="agent-embedded-title">原始数据</div>
        <div style="font-size:0.8rem;color:var(--warning);margin-bottom:8px;">${escapeHtml(message)}</div>
        <pre class="raw-json-block">${escapeHtml(text)}</pre>`;
}

function renderAttractionDetail(data) {
    const trace = data._trace || {};
    const attractions = data.attractions || [];
    let html = `<div class="agent-embedded-title">景点 POI 调用链路</div>`;
    html += `<div class="trace-grid">
        <div class="trace-item"><div class="trace-value">${trace.local_count ?? 0}</div><div class="trace-label">本地 DB</div></div>
        <div class="trace-item"><div class="trace-value">${trace.cached_count ?? 0}</div><div class="trace-label">缓存命中</div></div>
        <div class="trace-item"><div class="trace-value">${trace.fetched_count ?? 0}</div><div class="trace-label">高德/百度补充</div></div>
        <div class="trace-item"><div class="trace-value">${trace.final_count ?? 0}</div><div class="trace-label">最终候选</div></div>
    </div>`;
    html += `<div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:12px;">
        API 调用: ${trace.api_called ? (trace.api_provider || '地图API') : '未调用'}
        · 缓存更新: ${trace.cache_updated ? '是' : '否'}
        ${trace.api_error ? `<br><span style="color:var(--danger);">&#9888; ${escapeHtml(trace.api_error)}</span>` : ''}
        ${trace.warning ? `<br><span style="color:var(--danger);">&#9888; ${escapeHtml(trace.warning)}</span>` : ''}
    </div>`;
    if (attractions.length) {
        html += `<table class="poi-table"><thead><tr>
            <th>名称</th><th>地址</th><th>评分</th><th>来源</th>
        </tr></thead><tbody>`;
        attractions.forEach(a => {
            const srcClass = a.source === 'local_db' ? 'source-local' : 'source-map';
            const srcText = a.source === 'local_db' ? '本地' : '高德/百度';
            html += `<tr>
                <td>${escapeHtml(a.name)}</td>
                <td>${escapeHtml(a.address || '-')}</td>
                <td>${a.rating != null ? a.rating : '-'}</td>
                <td><span class="source-tag ${srcClass}">${srcText}</span></td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }
    return html;
}

function renderHotelDetail(data) {
    const hotels = data.hotels || [];
    let html = `<div class="agent-embedded-title">酒店候选（${data.total ?? hotels.length}）</div>`;
    if (data.api_error) {
        html += `<div style="font-size:0.8rem;color:var(--danger);margin-bottom:12px;">&#9888; ${escapeHtml(data.api_error)}</div>`;
    }
    hotels.forEach(h => {
        const xhs = h.xiaohongshu || {};
        html += `<div class="agent-list-item">
            <div style="font-weight:600;">${escapeHtml(h.name)}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary);">
                评分 ${h.rating != null ? h.rating : '-'} ·
                价格 ${h.price_value != null ? '¥' + h.price_value : '-'} ·
                小红书可信度 ${xhs.credibility_score != null ? xhs.credibility_score : '-'}
            </div>
        </div>`;
    });
    return html;
}

function renderRestaurantDetail(data) {
    const restaurants = data.restaurants || [];
    let html = `<div class="agent-embedded-title">餐厅候选（${data.total ?? restaurants.length}）</div>`;
    if (data.api_error) {
        html += `<div style="font-size:0.8rem;color:var(--danger);margin-bottom:12px;">&#9888; ${escapeHtml(data.api_error)}</div>`;
    }
    restaurants.forEach(r => {
        html += `<div class="agent-list-item">
            <div style="font-weight:600;">${escapeHtml(r.name)}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary);">
                评分 ${r.rating != null ? r.rating : '-'} ·
                价格 ${r.price_value != null ? '¥' + r.price_value : '-'} ·
                ${escapeHtml(r.district || '')}
            </div>
        </div>`;
    });
    return html;
}

function getWeatherIcon(description) {
    const d = (description || '').toLowerCase();
    if (d.includes('雨')) return '🌧️';
    if (d.includes('雪')) return '❄️';
    if (d.includes('云') || d.includes('阴')) return '☁️';
    if (d.includes('雾') || d.includes('霾')) return '🌫️';
    if (d.includes('雷')) return '⛈️';
    if (d.includes('晴')) return '☀️';
    return '🌤️';
}

function renderWeatherDetail(data) {
    const current = data.current || {};
    const forecast = data.forecast || [];
    let html = `<div class="agent-embedded-title">天气详情</div>
    <div class="weather-detail-current">
        <div class="weather-current-main">
            <div class="weather-current-icon">${getWeatherIcon(current.description)}</div>
            <div class="weather-current-temp">${current.temp != null ? current.temp + '°' : '?'}C</div>
        </div>
        <div class="weather-current-meta">
            <div class="weather-meta-item">
                <div class="weather-meta-label">天气</div>
                <div class="weather-meta-value">${escapeHtml(current.description || '-')}</div>
            </div>
            <div class="weather-meta-item">
                <div class="weather-meta-label">湿度</div>
                <div class="weather-meta-value">${current.humidity != null ? current.humidity + '%' : '-'}</div>
            </div>
            <div class="weather-meta-item">
                <div class="weather-meta-label">风速</div>
                <div class="weather-meta-value">${current.wind_speed != null ? current.wind_speed + 'm/s' : '-'}</div>
            </div>
            <div class="weather-meta-item">
                <div class="weather-meta-label">来源</div>
                <div class="weather-meta-value">${escapeHtml(current.provider || data.source || 'mock')}</div>
            </div>
        </div>
        ${data.clothing_advice ? `<div class="weather-advice">💡 穿衣建议：${escapeHtml(data.clothing_advice)}</div>` : ''}
    </div>`;

    if (forecast.length) {
        html += `<div class="weather-forecast-title">未来预报</div>
        <div class="weather-forecast">`;
        forecast.forEach(f => {
            html += `
            <div class="weather-day-card">
                <div class="weather-day-date">${escapeHtml(f.date || '-')}</div>
                <div class="weather-day-icon">${getWeatherIcon(f.description)}</div>
                <div class="weather-day-desc">${escapeHtml(f.description || '-')}</div>
                <div class="weather-day-temp">${f.temp_max != null ? f.temp_max + '°' : '-'} / ${f.temp_min != null ? f.temp_min + '°' : '-'}</div>
            </div>`;
        });
        html += `</div>`;
    }
    return html;
}

function renderTransportDetail(data) {
    const options = data.options || [];
    let html = `<div class="agent-embedded-title">交通方案（${data.distance_km != null ? data.distance_km + ' km' : '-'}）</div>`;
    options.forEach(o => {
        html += `<div class="agent-list-item">
            <div style="font-weight:600;">${escapeHtml(o.name)}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary);">
                ${o.type || ''} · 约${o.duration != null ? o.duration + '分钟' : '-'} · ¥${o.price != null ? o.price : '-'}
            </div>
        </div>`;
    });
    return html;
}

function renderRiskDetail(data) {
    const warnings = data.warnings || [];
    let html = `<div class="agent-embedded-title">风控检查</div>`;
    if (!warnings.length) {
        return html + `<div class="text-muted" style="font-size:0.85rem;">暂未发现风险</div>`;
    }
    warnings.forEach(w => {
        const tagClass = {high: 'tag-danger', medium: 'tag-warning', low: 'tag-info'}[w.level] || 'tag-warning';
        html += `<div class="agent-list-item">
            <span class="tag ${tagClass}">${escapeHtml(w.type)}</span>
            <div style="font-weight:600;margin-top:4px;">${escapeHtml(w.message)}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary);">建议：${escapeHtml(w.suggestion || '')}</div>
        </div>`;
    });
    return html;
}

function renderPlanningTrace(trace) {
    return `
    <div class="card planning-trace-card animate-fade-up animate-delay-1">
        <div class="trace-header" onclick="togglePlanningTrace()">
            <div class="trace-header-left">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/>
                </svg>
                Planner 思考链
                <span class="trace-count">${trace.length} 步</span>
            </div>
            <svg class="trace-chevron" id="planning-trace-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6 9 12 15 18 9"/>
            </svg>
        </div>
        <div class="trace-timeline" id="planning-trace-timeline" style="display:none;">
            ${trace.map((step, idx) => renderTraceStep(step, idx)).join('')}
        </div>
    </div>`;
}

function renderTraceStep(step, idx) {
    const type = step.type || 'thought';
    const icon = type === 'thought' ? '💡' : type === 'tool_call' ? '🔧' : '📥';
    const title = type === 'thought'
        ? '思考'
        : type === 'tool_call'
            ? `调用 ${step.tool || ''}`
            : `观察 ${step.tool || ''}`;
    const content = formatTraceContent(step);
    return `
    <div class="trace-step ${type}">
        <div class="trace-step-icon">${icon}</div>
        <div class="trace-step-body">
            <div class="trace-step-title">${idx + 1}. ${escapeHtml(title)}</div>
            ${content ? `<div class="trace-step-content">${escapeHtml(content)}</div>` : ''}
        </div>
    </div>`;
}

function renderWeatherCard(weather) {
    const d = weather.data || {};
    const current = d.current || {};
    return `
    <div class="card weather-card animate-fade-up animate-delay-1">
        <div class="card-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="5"/>
                <line x1="12" y1="1" x2="12" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="23"/>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
                <line x1="1" y1="12" x2="3" y2="12"/>
                <line x1="21" y1="12" x2="23" y2="12"/>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
            </svg>
            目的地天气
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:24px;flex-wrap:wrap;">
            <div>
                <div style="font-size:2.8rem;font-weight:300;font-family:var(--font-display);">${current.temp || '?'}°C</div>
                <div style="opacity:0.8;">${current.description || ''}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:0.85rem;opacity:0.7;">湿度 ${current.humidity || '?'}%</div>
                <div style="font-size:0.85rem;opacity:0.7;">风速 ${current.wind_speed || '?'}m/s</div>
                <div style="font-size:0.85rem;opacity:0.7;margin-top:8px;">${d.clothing_advice || ''}</div>
                <div style="font-size:0.7rem;opacity:0.5;margin-top:8px;">数据来源：${current.provider || 'mock'}</div>
            </div>
        </div>
    </div>`;
}
