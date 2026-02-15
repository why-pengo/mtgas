/**
 * MTG Arena Statistics - D3.js Visualizations
 *
 * Provides interactive charts for win rates, deck usage, color distribution, etc.
 */

const MTGACharts = {
    // Color palette for MTG colors
    mtgColors: {
        W: '#F8E7B9',  // White
        U: '#0E68AB',  // Blue
        B: '#150B00',  // Black
        R: '#D3202A',  // Red
        G: '#00733E',  // Green
        C: '#888888',  // Colorless
    },

    // General chart colors
    chartColors: {
        primary: '#e94560',
        secondary: '#4caf50',
        tertiary: '#2196f3',
        background: '#16213e',
        grid: 'rgba(255,255,255,0.1)',
        text: '#888888',
    },

    /**
     * Create a win rate bar chart
     * @param {string} selector - CSS selector for container
     * @param {Array} data - Array of {label, wins, losses, games}
     */
    winRateBarChart: function(selector, data) {
        const container = d3.select(selector);
        container.selectAll('*').remove();

        const margin = {top: 20, right: 30, bottom: 60, left: 50};
        const width = container.node().getBoundingClientRect().width - margin.left - margin.right;
        const height = 300 - margin.top - margin.bottom;

        const svg = container.append('svg')
            .attr('width', width + margin.left + margin.right)
            .attr('height', height + margin.top + margin.bottom)
            .append('g')
            .attr('transform', `translate(${margin.left},${margin.top})`);

        // Calculate win rates
        data.forEach(d => {
            d.winRate = d.games > 0 ? (d.wins / d.games * 100) : 0;
        });

        // Scales
        const x = d3.scaleBand()
            .domain(data.map(d => d.label))
            .range([0, width])
            .padding(0.2);

        const y = d3.scaleLinear()
            .domain([0, 100])
            .range([height, 0]);

        // Grid lines
        svg.append('g')
            .attr('class', 'grid')
            .call(d3.axisLeft(y)
                .tickSize(-width)
                .tickFormat('')
            )
            .selectAll('line')
            .style('stroke', this.chartColors.grid);

        // Bars
        svg.selectAll('.bar')
            .data(data)
            .enter()
            .append('rect')
            .attr('class', 'bar')
            .attr('x', d => x(d.label))
            .attr('width', x.bandwidth())
            .attr('y', d => y(d.winRate))
            .attr('height', d => height - y(d.winRate))
            .attr('fill', d => d.winRate >= 50 ? this.chartColors.secondary : this.chartColors.primary)
            .attr('rx', 4)
            .on('mouseover', function(event, d) {
                d3.select(this).attr('opacity', 0.8);
                MTGACharts.showTooltip(event, `${d.label}<br>Win Rate: ${d.winRate.toFixed(1)}%<br>Record: ${d.wins}W - ${d.losses}L`);
            })
            .on('mouseout', function() {
                d3.select(this).attr('opacity', 1);
                MTGACharts.hideTooltip();
            });

        // Win rate labels on bars
        svg.selectAll('.bar-label')
            .data(data)
            .enter()
            .append('text')
            .attr('class', 'bar-label')
            .attr('x', d => x(d.label) + x.bandwidth() / 2)
            .attr('y', d => y(d.winRate) - 5)
            .attr('text-anchor', 'middle')
            .attr('fill', this.chartColors.text)
            .attr('font-size', '12px')
            .text(d => d.games > 0 ? `${d.winRate.toFixed(0)}%` : '-');

        // X Axis
        svg.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', this.chartColors.text)
            .attr('transform', 'rotate(-45)')
            .style('text-anchor', 'end');

        // Y Axis
        svg.append('g')
            .call(d3.axisLeft(y).ticks(5).tickFormat(d => d + '%'))
            .selectAll('text')
            .attr('fill', this.chartColors.text);

        // 50% reference line
        svg.append('line')
            .attr('x1', 0)
            .attr('x2', width)
            .attr('y1', y(50))
            .attr('y2', y(50))
            .attr('stroke', '#ff9800')
            .attr('stroke-dasharray', '5,5')
            .attr('opacity', 0.5);
    },

    /**
     * Create a deck usage pie chart
     * @param {string} selector - CSS selector for container
     * @param {Array} data - Array of {label, value}
     */
    deckUsagePieChart: function(selector, data) {
        const container = d3.select(selector);
        container.selectAll('*').remove();

        const width = container.node().getBoundingClientRect().width;
        const height = 300;
        const radius = Math.min(width, height) / 2 - 40;

        const svg = container.append('svg')
            .attr('width', width)
            .attr('height', height)
            .append('g')
            .attr('transform', `translate(${width / 2},${height / 2})`);

        // Color scale
        const color = d3.scaleOrdinal()
            .domain(data.map(d => d.label))
            .range(d3.schemeSet2);

        // Pie generator
        const pie = d3.pie()
            .value(d => d.value)
            .sort(null);

        // Arc generator
        const arc = d3.arc()
            .innerRadius(radius * 0.5)  // Donut chart
            .outerRadius(radius);

        const arcHover = d3.arc()
            .innerRadius(radius * 0.5)
            .outerRadius(radius * 1.05);

        // Draw slices
        const slices = svg.selectAll('.slice')
            .data(pie(data))
            .enter()
            .append('g')
            .attr('class', 'slice');

        slices.append('path')
            .attr('d', arc)
            .attr('fill', d => color(d.data.label))
            .attr('stroke', this.chartColors.background)
            .attr('stroke-width', 2)
            .on('mouseover', function(event, d) {
                d3.select(this).transition().duration(200).attr('d', arcHover);
                const total = data.reduce((sum, item) => sum + item.value, 0);
                const pct = (d.data.value / total * 100).toFixed(1);
                MTGACharts.showTooltip(event, `${d.data.label}<br>Games: ${d.data.value} (${pct}%)`);
            })
            .on('mouseout', function() {
                d3.select(this).transition().duration(200).attr('d', arc);
                MTGACharts.hideTooltip();
            });

        // Labels
        const labelArc = d3.arc()
            .innerRadius(radius * 0.8)
            .outerRadius(radius * 0.8);

        slices.append('text')
            .attr('transform', d => `translate(${labelArc.centroid(d)})`)
            .attr('text-anchor', 'middle')
            .attr('fill', '#fff')
            .attr('font-size', '11px')
            .text(d => {
                const total = data.reduce((sum, item) => sum + item.value, 0);
                const pct = d.data.value / total * 100;
                return pct > 5 ? `${pct.toFixed(0)}%` : '';
            });

        // Legend
        const legend = svg.selectAll('.legend')
            .data(data)
            .enter()
            .append('g')
            .attr('class', 'legend')
            .attr('transform', (d, i) => `translate(${radius + 20}, ${-radius + 20 + i * 20})`);

        legend.append('rect')
            .attr('width', 12)
            .attr('height', 12)
            .attr('fill', d => color(d.label))
            .attr('rx', 2);

        legend.append('text')
            .attr('x', 18)
            .attr('y', 10)
            .attr('fill', this.chartColors.text)
            .attr('font-size', '11px')
            .text(d => d.label.length > 15 ? d.label.substring(0, 15) + '...' : d.label);
    },

    /**
     * Create a color distribution chart (horizontal stacked bar)
     * @param {string} selector - CSS selector for container
     * @param {Object} colorCounts - {W: count, U: count, B: count, R: count, G: count}
     */
    colorDistributionChart: function(selector, colorCounts) {
        const container = d3.select(selector);
        container.selectAll('*').remove();

        const width = container.node().getBoundingClientRect().width;
        const height = 60;

        const svg = container.append('svg')
            .attr('width', width)
            .attr('height', height);

        const colors = ['W', 'U', 'B', 'R', 'G'];
        const total = colors.reduce((sum, c) => sum + (colorCounts[c] || 0), 0);

        if (total === 0) {
            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height / 2)
                .attr('text-anchor', 'middle')
                .attr('fill', this.chartColors.text)
                .text('No color data');
            return;
        }

        let x = 0;
        const barHeight = 30;
        const y = (height - barHeight) / 2;

        colors.forEach(color => {
            const count = colorCounts[color] || 0;
            if (count === 0) return;

            const barWidth = (count / total) * width;

            svg.append('rect')
                .attr('x', x)
                .attr('y', y)
                .attr('width', barWidth)
                .attr('height', barHeight)
                .attr('fill', this.mtgColors[color])
                .attr('stroke', color === 'B' ? '#444' : 'none')
                .on('mouseover', (event) => {
                    const pct = (count / total * 100).toFixed(1);
                    const colorNames = {W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green'};
                    MTGACharts.showTooltip(event, `${colorNames[color]}: ${count} (${pct}%)`);
                })
                .on('mouseout', () => MTGACharts.hideTooltip());

            // Label if segment is wide enough
            if (barWidth > 30) {
                svg.append('text')
                    .attr('x', x + barWidth / 2)
                    .attr('y', y + barHeight / 2 + 4)
                    .attr('text-anchor', 'middle')
                    .attr('fill', color === 'W' ? '#333' : '#fff')
                    .attr('font-size', '12px')
                    .attr('font-weight', 'bold')
                    .text(color);
            }

            x += barWidth;
        });
    },

    /**
     * Create a win rate over time line chart
     * @param {string} selector - CSS selector for container
     * @param {Array} data - Array of {date, winRate, games}
     */
    winRateTimeChart: function(selector, data) {
        const container = d3.select(selector);
        container.selectAll('*').remove();

        if (!data || data.length === 0) {
            container.append('p')
                .attr('class', 'text-muted text-center')
                .text('No data available');
            return;
        }

        const margin = {top: 20, right: 60, bottom: 30, left: 50};
        const width = container.node().getBoundingClientRect().width - margin.left - margin.right;
        const height = 250 - margin.top - margin.bottom;

        const svg = container.append('svg')
            .attr('width', width + margin.left + margin.right)
            .attr('height', height + margin.top + margin.bottom)
            .append('g')
            .attr('transform', `translate(${margin.left},${margin.top})`);

        // Parse dates
        const parseDate = d3.timeParse('%Y-%m-%d');
        data.forEach(d => {
            d.dateObj = parseDate(d.date);
        });

        // Scales
        const x = d3.scaleTime()
            .domain(d3.extent(data, d => d.dateObj))
            .range([0, width]);

        const y = d3.scaleLinear()
            .domain([0, 100])
            .range([height, 0]);

        const y2 = d3.scaleLinear()
            .domain([0, d3.max(data, d => d.games) * 1.2])
            .range([height, 0]);

        // Grid
        svg.append('g')
            .attr('class', 'grid')
            .call(d3.axisLeft(y).tickSize(-width).tickFormat(''))
            .selectAll('line')
            .style('stroke', this.chartColors.grid);

        // Area for win rate
        const area = d3.area()
            .x(d => x(d.dateObj))
            .y0(height)
            .y1(d => y(d.winRate))
            .curve(d3.curveMonotoneX);

        svg.append('path')
            .datum(data)
            .attr('fill', 'rgba(233, 69, 96, 0.2)')
            .attr('d', area);

        // Win rate line
        const line = d3.line()
            .x(d => x(d.dateObj))
            .y(d => y(d.winRate))
            .curve(d3.curveMonotoneX);

        svg.append('path')
            .datum(data)
            .attr('fill', 'none')
            .attr('stroke', this.chartColors.primary)
            .attr('stroke-width', 2)
            .attr('d', line);

        // Games bars
        const barWidth = Math.max(width / data.length - 4, 2);
        svg.selectAll('.games-bar')
            .data(data)
            .enter()
            .append('rect')
            .attr('class', 'games-bar')
            .attr('x', d => x(d.dateObj) - barWidth / 2)
            .attr('y', d => y2(d.games))
            .attr('width', barWidth)
            .attr('height', d => height - y2(d.games))
            .attr('fill', this.chartColors.secondary)
            .attr('opacity', 0.5);

        // Points
        svg.selectAll('.point')
            .data(data)
            .enter()
            .append('circle')
            .attr('class', 'point')
            .attr('cx', d => x(d.dateObj))
            .attr('cy', d => y(d.winRate))
            .attr('r', 4)
            .attr('fill', this.chartColors.primary)
            .on('mouseover', (event, d) => {
                MTGACharts.showTooltip(event,
                    `${d.date}<br>Win Rate: ${d.winRate.toFixed(1)}%<br>Games: ${d.games}`);
            })
            .on('mouseout', () => MTGACharts.hideTooltip());

        // 50% reference line
        svg.append('line')
            .attr('x1', 0)
            .attr('x2', width)
            .attr('y1', y(50))
            .attr('y2', y(50))
            .attr('stroke', '#ff9800')
            .attr('stroke-dasharray', '5,5')
            .attr('opacity', 0.5);

        // Axes
        svg.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x).ticks(7).tickFormat(d3.timeFormat('%m/%d')))
            .selectAll('text')
            .attr('fill', this.chartColors.text);

        svg.append('g')
            .call(d3.axisLeft(y).ticks(5).tickFormat(d => d + '%'))
            .selectAll('text')
            .attr('fill', this.chartColors.text);

        svg.append('g')
            .attr('transform', `translate(${width}, 0)`)
            .call(d3.axisRight(y2).ticks(5))
            .selectAll('text')
            .attr('fill', this.chartColors.text);

        // Axis labels
        svg.append('text')
            .attr('x', -10)
            .attr('y', -8)
            .attr('fill', this.chartColors.text)
            .attr('font-size', '11px')
            .text('Win %');

        svg.append('text')
            .attr('x', width + 10)
            .attr('y', -8)
            .attr('fill', this.chartColors.text)
            .attr('font-size', '11px')
            .text('Games');
    },

    /**
     * Create a mana curve bar chart
     * @param {string} selector - CSS selector for container
     * @param {Object} manaCurve - {0: count, 1: count, ..., 7: count}
     */
    manaCurveChart: function(selector, manaCurve) {
        const container = d3.select(selector);
        container.selectAll('*').remove();

        const margin = {top: 20, right: 20, bottom: 40, left: 40};
        const width = container.node().getBoundingClientRect().width - margin.left - margin.right;
        const height = 200 - margin.top - margin.bottom;

        const svg = container.append('svg')
            .attr('width', width + margin.left + margin.right)
            .attr('height', height + margin.top + margin.bottom)
            .append('g')
            .attr('transform', `translate(${margin.left},${margin.top})`);

        const data = Object.entries(manaCurve).map(([cmc, count]) => ({
            cmc: parseInt(cmc),
            count: count,
            label: cmc === '7' ? '7+' : cmc
        }));

        const x = d3.scaleBand()
            .domain(data.map(d => d.label))
            .range([0, width])
            .padding(0.2);

        const y = d3.scaleLinear()
            .domain([0, d3.max(data, d => d.count) * 1.1])
            .range([height, 0]);

        // Bars
        svg.selectAll('.bar')
            .data(data)
            .enter()
            .append('rect')
            .attr('class', 'bar')
            .attr('x', d => x(d.label))
            .attr('width', x.bandwidth())
            .attr('y', d => y(d.count))
            .attr('height', d => height - y(d.count))
            .attr('fill', this.chartColors.primary)
            .attr('rx', 3);

        // Count labels
        svg.selectAll('.count-label')
            .data(data)
            .enter()
            .append('text')
            .attr('x', d => x(d.label) + x.bandwidth() / 2)
            .attr('y', d => y(d.count) - 5)
            .attr('text-anchor', 'middle')
            .attr('fill', this.chartColors.text)
            .attr('font-size', '11px')
            .text(d => d.count > 0 ? d.count : '');

        // X Axis
        svg.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', this.chartColors.text);

        svg.append('text')
            .attr('x', width / 2)
            .attr('y', height + 35)
            .attr('text-anchor', 'middle')
            .attr('fill', this.chartColors.text)
            .attr('font-size', '12px')
            .text('Mana Value');

        // Y Axis
        svg.append('g')
            .call(d3.axisLeft(y).ticks(5))
            .selectAll('text')
            .attr('fill', this.chartColors.text);
    },

    /**
     * Show tooltip
     */
    showTooltip: function(event, html) {
        let tooltip = d3.select('#d3-tooltip');
        if (tooltip.empty()) {
            tooltip = d3.select('body').append('div')
                .attr('id', 'd3-tooltip')
                .style('position', 'absolute')
                .style('background', 'rgba(0,0,0,0.9)')
                .style('color', '#fff')
                .style('padding', '8px 12px')
                .style('border-radius', '4px')
                .style('font-size', '12px')
                .style('pointer-events', 'none')
                .style('z-index', '1000');
        }
        tooltip
            .html(html)
            .style('left', (event.pageX + 10) + 'px')
            .style('top', (event.pageY - 10) + 'px')
            .style('opacity', 1);
    },

    /**
     * Hide tooltip
     */
    hideTooltip: function() {
        d3.select('#d3-tooltip').style('opacity', 0);
    }
};

