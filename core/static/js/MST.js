/**
 * @file Minimum Spanning Tree (MST) Visualization for Genomic Isolate Tracking.
 * Resolves genomic similarities, renders interactive force layouts, handles
 * multi-tab UI synchronization, and profiles algorithmic epidemic clusters.
 * * @requires d3.js v7+
 */

// ==========================================
// --- Global Configurations & UI Caches ---
// ==========================================

/** Initial width and height for the SVG */
let width = 900;
let height = 800;

/** Get container element and initialize SVG with groups */
const container = document.getElementById("mst-container");
const svg = d3.select("#mst").attr("width", width).attr("height", height);
const g = svg.select(".zoom-group").size() ? svg.select(".zoom-group") : svg.append("g").attr("class", "zoom-group");

/** Initialize tooltip element */
const tooltip = d3.select("body")
    .append("div")
    .attr("class", "tooltip")
    .style("opacity", 0);

// ===================================
// --- D3 Scales, Enums & State ---
// ===================================

const clusterColors = d3.scaleOrdinal(d3.schemeCategory10);
const clusterShapes = [
    d3.symbolCircle, d3.symbolSquare, d3.symbolTriangle,
    d3.symbolDiamond, d3.symbolCross, d3.symbolStar, d3.symbolWye
];

/** Linearly maps genetic locus weight differences to relative structural attraction bounds.
 * Closer connections maintain firm visual shapes while distant ones are permitted to drift flexibly.
 */
const linkStrengthScale = d3.scaleLinear()
    .domain([0, 1, 2, 3, 10])
    .range([1.0, 0.4, 0.1, 0.01])
    .clamp(true);

const symbolGenerator = d3.symbol();

let globalSimulation;
let globalNodes = [];
let globalLinks = [];
let isInitialLoad = true;
let mlvaNodes = [];
let mlvaLinks = [];
let inactiveIsolates = [];
let inactiveIsolatesChecksum = "";
let currentDistanceCutoff = 5; 
let currentTreeAlgorithm = "kruskal";

// ==========================
// --- Zoom Configurations ---
// ==========================

/** Configured transformation boundaries mapping zoom operations onto the layout container */
const zoom = d3.zoom()
    .scaleExtent([0.05, 12])
    .on("zoom", (event) => {
        g.attr("transform", event.transform);
    });

svg.call(zoom);

const zoomStep = 1.3;

// ====================================
// --- Control Panel Event Handlers ---
// ====================================

d3.select("#zoom-in").on("click", () => {
    svg.transition().duration(250).call(zoom.scaleBy, zoomStep);
});

d3.select("#zoom-out").on("click", () => {
    svg.transition().duration(250).call(zoom.scaleBy, 1 / zoomStep);
});

d3.select("#zoom-reset").on("click", () => {
    fitToView();
});

d3.select("#zoom-layout-reset").on("click", () => {
    // Unfix all node configurations cleanly
    globalNodes.forEach(n => { 
        n.fx = null; 
        n.fy = null; 
        delete n.baseX;
        delete n.baseY;
    });
    // Strip pinned visual state styling classes 
    d3.selectAll(".node").classed("pinned", false);
    // Restart physics engine and update background cluster hulls smoothly
    globalSimulation.alpha(0.3).restart();
});

d3.select("#export-clusters-csv").on("click", () => {
    if (!globalNodes || globalNodes.length === 0) {
        alert("No node data available to export.");
        return;
    }

    // Get the currently selected clustering algorithm variant for the filename
    const algoSelect = document.getElementById("cluster-algorithm");
    const activeMode = algoSelect ? algoSelect.value : "none";

    // 1. Identify all unique locus keys present across the dataset to build dynamic headers
    const allLocusNames = new Set();
    globalNodes.forEach(node => {
        if (node.locus_results) {
            Object.keys(node.locus_results).forEach(locus => allLocusNames.add(locus));
        }
    });
    const sortedLoci = Array.from(allLocusNames).sort();

    // 2. Build CSV Document Headers
    const headers = [
        "Isolate Label",
        "Cluster ID",
        "Sampling Location",
        "Sampling Date",
        "Contig Count",
        "N50 (bp)",
        "Assembly Size (bp)",
        ...sortedLoci
    ];

    // Helper helper to safely escape text fields that might contain commas or quotes
    const escapeCSV = (val) => {
        if (val === undefined || val === null) return '""';
        const str = String(val).replace(/"/g, '""');
        return `"${str}"`;
    };

    // 3. Map Node records down into structured row strings
    const rows = globalNodes.map(node => {
        const rowData = [
            escapeCSV(node.label),
            escapeCSV(node.clusterId !== undefined && node.clusterId !== null ? node.clusterId : "Unclustered/None"),
            escapeCSV(node.sampling_location || "N/A"),
            escapeCSV(node.sampling_date || "N/A"),
            escapeCSV(node.contig_count || 0),
            escapeCSV(node.n50 || "N/A"),
            escapeCSV(node.assembly_size || "N/A")
        ];

        // Append the matching repeat counts corresponding to each sorted locus header column
        sortedLoci.forEach(locus => {
            const count = node.locus_results && node.locus_results[locus] !== undefined ? node.locus_results[locus] : "";
            rowData.push(escapeCSV(count));
        });

        return rowData.join(",");
    });

    // Combine headers and data rows into a single string element payload block
    const csvContent = [headers.join(","), ...rows].join("\n");

    // 4. Generate a sandboxed document download action trigger payload
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    
    if (link.download !== undefined) { 
        const url = URL.createObjectURL(blob);
        link.setAttribute("href", url);
        
        // Generate a descriptive, timestamped export filename
        const timestamp = new Date().toISOString().slice(0, 10);
        link.setAttribute("download", `cdiff_clusters_${activeMode}_${timestamp}.csv`);
        
        link.style.visibility = "hidden";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url); // Clean up the memory reference
    }
});

/** Reference trigger handling layout element fullscreen switches */
const fullscreenBtn = d3.select("#fullscreen-toggle");

fullscreenBtn.on("click", async () => {
    // TARGET FIX: Target the visualization sub-panel instead of the root container
    const targetVisualPanel = document.getElementById("mst-visualization");
    if (!targetVisualPanel) return;

    try {
        if (!document.fullscreenElement) {
            await targetVisualPanel.requestFullscreen();
            fullscreenBtn.text("🡼");
            // Force the controls panel to take a standard absolute position context
            d3.select(".controls-panel").style("position", "fixed");
        } else {
            await document.exitFullscreen();
            fullscreenBtn.text("⛶");
            d3.select(".controls-panel").style("position", "absolute");
        }
    } catch (err) {
        console.error("Fullscreen change error:", err);
    }
});

// Structural layout listener verifying container switches
document.addEventListener("fullscreenchange", () => {
    requestAnimationFrame(() => {
        const panel = d3.select(".controls-panel");
        
        if (!document.fullscreenElement) {
            fullscreenBtn.text("⛶");
            panel.style("position", "absolute");
        } else {
            fullscreenBtn.text("🡼");
            panel.style("position", "fixed");
        }
        
        fitSvgToContainer();
        fitToView();
        
        if (globalSimulation) {
            globalSimulation.alpha(0.05).restart();
        }
    });
});


// ==========================
// --- Responsive Layout ---
// ==========================

/**
 * Recalculates viewport dimensions relative to its layout bounding container.
 * Updates internal dimensional bounds and SVG tag structures uniformly.
 */
function fitSvgToContainer() {
    const visualPanel = document.getElementById("mst-visualization");
    if (!visualPanel) return;

    // If fullscreen is active, use the screen width/height, otherwise use standard element geometry
    if (document.fullscreenElement) {
        width = window.innerWidth;
        height = window.innerHeight;
    } else {
        const rect = visualPanel.getBoundingClientRect();
        width = rect.width || 900;
        height = rect.height || 800;
    }
    
    svg.attr("width", width).attr("height", height);
}

// ================================
// --- Lifecycle Initialization ---
// ================================

// Triggers pipeline when DOM elements settle securely
document.addEventListener('DOMContentLoaded', initializeTreeView);

/**
 * Boots core structural variables, parses system DOM configurations,
 * fetches server data profiles, and initializes visualization targets.
 */
function initializeTreeView() {
    if (!container) return;
    const investigationId = container.getAttribute('data-investigation-id');
    const mlvaConfigId = container.getAttribute('data-mlva-config-id');
    
    fitSvgToContainer();

    // Listener for Distance Cutoff Sliders
    d3.select("#distance-cutoff").on("change", function() {
        currentDistanceCutoff = parseInt(this.value, 10);
        processAndRenderTree(); 
    });

    // NEW: Listener for Tree Algorithm Dropdowns
    d3.select("#tree-algorithm").on("change", function() {
        currentTreeAlgorithm = this.value;
        processAndRenderTree(); // Re-calculates network math instantly
    });

    fetch(`/investigations/${investigationId}/mlva/${mlvaConfigId}/tree-data/`)
        .then(response => {
            if (!response.ok) throw new Error(`Failed to fetch: ${response.statusText}`);
            return response.json();
        })
        .then(data => {
            mlvaNodes = data.nodes || [];
            mlvaLinks = data.links || [];
            inactiveIsolates = data.inactive_isolates || [];
            inactiveIsolatesChecksum = computeChecksum(inactiveIsolates);
            
            processAndRenderTree();
            setupTabVisibilityObserver();
        })
        .catch(error => console.error('Error initializing tree view:', error));
}

function processAndRenderTree() {
    const filteredLinks = mlvaLinks.filter(link => {
        let trueAlleleDistance = 0;

        if (link.per_locus) {
            // Count how many individual loci actually changed
            for (const [locus, value] of Object.entries(link.per_locus)) {
                // If the repeat value changed (i.e., difference is non-zero), it is 1 allele change
                if (Math.abs(value) > 0) {
                    trueAlleleDistance++;
                }
            }
        } else {
            // Fallback: If per_locus data isn't available for this link, default to the weight
            trueAlleleDistance = link.weight;
        }

        // Cache this true AD back onto the link object so the renderer can use it if needed
        link.trueAlleleDistance = trueAlleleDistance;

        // Apply the cutoff filter based on the number of mutated LOCI, not raw repeat counts
        return trueAlleleDistance <= currentDistanceCutoff;
    });
    
    // Stage 2: Route the filtered dataset to the selected structural topology calculator
    let structuralLinks = [];
    if (currentTreeAlgorithm === "kruskal") {
        structuralLinks = kruskal(mlvaNodes, filteredLinks);
    } else if (currentTreeAlgorithm === "prim") {
        structuralLinks = prim(mlvaNodes, filteredLinks);
    } else if (currentTreeAlgorithm === "single-linkage") {
        structuralLinks = singleLinkageNetwork(mlvaNodes, filteredLinks);
    } else if (currentTreeAlgorithm === "edmonds") {
        if (!selectedRootNodeId && mlvaNodes.length > 0) {
            selectedRootNodeId = mlvaNodes[0].id;
        }
        structuralLinks = edmondsArborescence(mlvaNodes, filteredLinks, selectedRootNodeId);
    }
    
    // Stage 3: Send processed calculations down the graphical rendering pipeline
    renderGraph(mlvaNodes, structuralLinks);
    
    // Stage 4: Maintain active cluster background hull updates seamlessly
    const algoSelect = document.getElementById("cluster-algorithm");
    if (algoSelect && algoSelect.value !== "none") {
        computeEpidemicClusters(algoSelect.value);
    }
}

// ===================================
// --- Dynamic Helper Calculators ---
// ===================================

/**
 * Assigns explicit geometric shape models depending on active partitions.
 * Loops structural indexing cleanly to prevent element rendering crashes.
 */
function getShapeForCluster(clusterId) {
    if (clusterId === undefined || clusterId === null) return d3.symbolCircle;
    const shapeIndex = Math.floor((clusterId - 1) / 10) % clusterShapes.length;
    return clusterShapes[shapeIndex];
}

/**
 * Calculates absolute rendering surface scale boundaries for target nodes
 * dependent on total relative internal sample elements tracked.
 */
function getSizeForNode(d) {
    const baseRadius = d.count ? Math.max(6, Math.sqrt(d.count) * 4) : 9;
    return Math.PI * Math.pow(baseRadius, 2);
}


/**
 * Re-injects tree definitions into memory structures securely without resetting
 * viewport properties or breaking target canvas transformations.
 */
function reloadTreeViewSilently(data) {
    mlvaNodes = data.nodes || [];
    mlvaLinks = data.links || [];
    
    inactiveIsolates = data.inactive_isolates || [];
    inactiveIsolatesChecksum = computeChecksum(inactiveIsolates);
    
    const countEl = document.getElementById('inactive-count');
    if (countEl) countEl.textContent = inactiveIsolates.length;
    
    // FIX: Process and render using our uniform filtering function instead of rendering raw links
    processAndRenderTree();
    
    d3.select("#node-sidebar")
        .classed("empty", true)
        .html('<div class="text-gray-400 text-sm text-center">Click on an isolate node to view details</div>');
}

/**
 * Registers tracking parameters checking if layout sections sit within view.
 * Enables background state tracking updates across separate tabs smoothly.
 */
function setupTabVisibilityObserver() {
    if (!container) return;
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) checkForInactiveIsolatesChanges();
        });
    }, { threshold: 0.1 });
    
    observer.observe(container);
}

/**
 * Validates tracking variables against remote values seamlessly without resetting active configurations.
 */
function checkForInactiveIsolatesChanges() {
    if (!container) return;
    const investigationId = container.getAttribute('data-investigation-id');
    const mlvaConfigId = container.getAttribute('data-mlva-config-id');
    
    fetch(`/investigations/${investigationId}/mlva/${mlvaConfigId}/tree-data/`)
        .then(response => {
            if (!response.ok) throw new Error('Failed to fetch tree data');
            return response.json();
        })
        .then(data => {
            const currentChecksum = computeChecksum(data.inactive_isolates);
            if (currentChecksum !== inactiveIsolatesChecksum) {
                reloadTreeViewSilently(data);
            }
        })
        .catch(error => console.error('Error checking updates silently:', error));
}

// ===============================
// --- Tree Drawing Algorithms ---
// ===============================

/**
 * Computes an acyclic Minimum Spanning Tree (MST) using Kruskal's algorithm.
 * Guarantees optimal path traversal over components, tracks disconnected node anomalies,
 * and handles path compression optimizations efficiently.
 */
function kruskal(nodes, links) {
    if (!links || links.length === 0) return [];
    
    const parent = {};
    const nodeIds = new Set(nodes.map(n => n.id));
    nodes.forEach(node => { parent[node.id] = node.id; });

    // Finds component roots with recursive path flattening optimizations
    function find(x) {
        let root = x;
        while (root !== parent[root]) { root = parent[root]; }
        let curr = x;
        while (curr !== root) { 
            let nxt = parent[curr];
            parent[curr] = root;
            curr = nxt;
        }
        return root;
    }

    // Connects tree segments; returns false if operations create looping networks
    function union(sourceId, targetId) {
        if (!nodeIds.has(sourceId) || !nodeIds.has(targetId)) return false;
        const rootA = find(sourceId);
        const rootB = find(targetId);
        if (rootA !== rootB) {
            parent[rootB] = rootA;
            return true;
        }
        return false;
    }

    // Normalize structural definitions safely and order connections by genetic drift distance
    const sortedLinks = links.map(link => ({
        ...link,
        source: typeof link.source === 'object' ? link.source.id : link.source,
        target: typeof link.target === 'object' ? link.target.id : link.target
    })).sort((a, b) => a.weight - b.weight);
    
    const mst = [];
    for (const link of sortedLinks) {
        if (union(link.source, link.target)) {
            mst.push(link);
            if (mst.length === nodes.length - 1) break; // Halts optimization cycles early when processing completes
        }
    }
    return mst;
}

function prim(nodes, links) {
    if (!nodes || nodes.length === 0 || !links || links.length === 0) return [];

    const mst = [];
    const visited = new Set();
    const nodeIds = new Set(nodes.map(n => n.id));

    // Normalize links for easy lookup
    const edgeList = links.map(link => ({
        ...link,
        source: typeof link.source === 'object' ? link.source.id : link.source,
        target: typeof link.target === 'object' ? link.target.id : link.target
    })).filter(l => nodeIds.has(l.source) && nodeIds.has(l.target));

    // Start tracking from the first node in the array
    visited.add(nodes[0].id);

    while (visited.size < nodes.length) {
        let minEdge = null;
        let minWeight = Infinity;

        // Find the absolute shortest edge connecting our visited pool to an unvisited node
        for (const edge of edgeList) {
            const sVisited = visited.has(edge.source);
            const tVisited = visited.has(edge.target);

            // The edge must connect the visited tree component to an unvisited leaf node
            if ((sVisited && !tVisited) || (!sVisited && tVisited)) {
                if (edge.weight < minWeight) {
                    minWeight = edge.weight;
                    minEdge = edge;
                }
            }
        }

        // If the graph is disconnected, break early to prevent an infinite loop
        if (!minEdge) break;

        mst.push(minEdge);
        visited.add(minEdge.source);
        visited.add(minEdge.target);
    }

    return mst;
}

function singleLinkageNetwork(nodes, links) {
    // This bypasses MST sorting entirely and returns every valid epidemiologically linked edge
    const nodeIds = new Set(nodes.map(n => n.id));
    return links.map(link => ({
        ...link,
        source: typeof link.source === 'object' ? link.source.id : link.source,
        target: typeof link.target === 'object' ? link.target.id : link.target
    })).filter(l => nodeIds.has(l.source) && nodeIds.has(l.target));
}

// ====================================
// --- Graphical Rendering Pipeline ---
// ====================================

/**
 * Clears old tracking structures, updates physics simulation setups,
 * and draws the nodes and edges onto the canvas.
 */
// ====================================
// --- Graphical Rendering Pipeline ---
// ====================================

function renderGraph(nodes, links) {
    globalNodes = nodes;
    globalLinks = links;

    // Arrange nodes in an algorithmic spiral pattern to distribute initial topology
    nodes.forEach((node, i) => {
        if (node.fx === null && node.fy === null) {
            const angle = i * 0.3;
            const radius = 40 * Math.sqrt(i) + 50; // Spreads them evenly outward
            node.x = width / 2 + radius * Math.cos(angle);
            node.y = height / 2 + radius * Math.sin(angle);
        }
    });

    // Clear previous DOM selections using our explicit group containers
    const bubbleLayer = d3.select("#cluster-bubbles");
    const linkLayer = d3.select("#links-group");
    const labelLayer = d3.select("#edge-labels-group");
    const nodeLayer = d3.select("#nodes-group");

    bubbleLayer.selectAll("*").remove();
    linkLayer.selectAll("*").remove();
    labelLayer.selectAll("*").remove();
    nodeLayer.selectAll("*").remove();

    const nodeDegrees = {};
    nodes.forEach(n => nodeDegrees[n.id] = 0);
    links.forEach(l => {
        // Handle both object references and raw IDs safely
        const sourceId = typeof l.source === 'object' ? l.source.id : l.source;
        const targetId = typeof l.target === 'object' ? l.target.id : l.target;
        
        nodeDegrees[sourceId]++;
        nodeDegrees[targetId]++;
    });

    // Configure structural physical properties mapping layouts
    globalSimulation = d3.forceSimulation(nodes)
        .alpha(1)             // Start with high layout energy
        .alphaMin(0.005)      // Force the simulation to calculate until it is tightly settled
        .alphaDecay(0.03)     // Cool down steadily
        .velocityDecay(0.6)   // High friction to stop late-stage orbiting and swinging
        .force("link", d3.forceLink(links)
            .id(d => d.id)
            .distance(d => 30 + (d.weight * 25))
            .strength(d => linkStrengthScale(d.weight)))
        .force("charge", d3.forceManyBody()
            .strength(d => {
                const degree = nodeDegrees[d.id] || 1;
                // Hub nodes (degree > 2) push out far away; leaf nodes stay close
                return degree > 2 ? -600 - (degree * 100) : -150;
            })
            .distanceMax(500))
        // CUSTOM CLUSTER FORCE: Pulls nodes of the same cluster together
        .force("cluster", (alpha) => {
            const centers = {};
            nodes.forEach(n => {
                if (n.clusterId && n.clusterId !== "none") {
                    if (!centers[n.clusterId]) centers[n.clusterId] = { x: 0, y: 0, count: 0 };
                    centers[n.clusterId].x += n.x;
                    centers[n.clusterId].y += n.y;
                    centers[n.clusterId].count++;
                }
            });

            Object.keys(centers).forEach(id => {
                centers[id].x /= centers[id].count;
                centers[id].y /= centers[id].count;
            });

            nodes.forEach(n => {
                if (n.clusterId && centers[n.clusterId]) {
                    const center = centers[n.clusterId];
                    // Smooth out transition using alpha squared (α²) to slow down attraction near the end
                    const clusterStrength = alpha * alpha * 0.5; 
                    n.vx += (center.x - n.x) * clusterStrength;
                    n.vy += (center.y - n.y) * clusterStrength;
                }
            });
        })
        // Enforce strong collision buffers preventing bubbles/nodes overlapping
        .force("collide", d3.forceCollide()
            .radius(d => (d.count ? Math.sqrt(d.count) * 4 : 9) + 12) 
            .iterations(3))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("x", d3.forceX(width / 2).strength(d => {
            // Nodes close to the center get pushed harder outward, creating breathing room
            return (Math.abs(d.x - width / 2) < 100) ? 0.08 : 0.01;
        }))
        .force("y", d3.forceY(height / 2).strength(d => {
            return (Math.abs(d.y - height / 2) < 100) ? 0.08 : 0.01;
        }))

    // Construct edge vector indicators with an explicit check for STRD == 0
    const link = linkLayer.selectAll("line")
        .data(links)
        .enter()
        .append("line")
        .attr("class", d => {
            let typeClass = "subvariant";
            if (d.weight === 0) typeClass = "idv";      // Identical Variant (STRD == 0)
            else if (d.weight === 1) typeClass = "slv"; // Single Locus Variant
            else if (d.weight === 2) typeClass = "dlv"; // Double Locus Variant
            else if (d.weight === 3) typeClass = "tlv"; // Triple Locus Variant
            return `link ${typeClass}`;
        });

    // Build container groupings tracking similarity markers across linkages
    const edgeLabelGroups = labelLayer.selectAll("g")
        .data(links)
        .enter()
        .append("g")
        .attr("class", "weight-label-group")
        .on("mouseover", function(event, d) {
            let locusHtml = "";
            if (d.per_locus) {
                for (const [locus, value] of Object.entries(d.per_locus)) {
                    locusHtml += `<div><strong>${locus}</strong>: ${value}</div>`;
                }
            }
            tooltip.style("opacity", 1)
                .html(`<div><strong>Locus Changes:</strong> ${d.weight}</div><hr>${locusHtml}`)
                .style("left", (event.pageX + 10) + "px")
                .style("top", (event.pageY + 10) + "px");
        })
        .on("mouseout", () => tooltip.style("opacity", 0));

    edgeLabelGroups.append("rect")
        .attr("class", "weight-label-bg")
        .attr("width", 22).attr("height", 14).attr("x", -11).attr("y", -7);

    edgeLabelGroups.append("text")
        .attr("class", "weight-label-text").text(d => d.weight);

    // Render interactive node items onto tracking canvas
    const node = nodeLayer.selectAll("g")
        .data(nodes)
        .enter()
        .append("g")
        .attr("class", "node")
        .call(drag(globalSimulation))
        .on("mouseover", function(event, d) {
            let content = `<div><strong>Isolate: ${d.label}</strong></div>`;
            if (d.count) content += `<div>Isolates Count: ${d.count}</div>`;
            if (d.clusterId != null) {
                content += `<div><span style='color:${clusterColors(d.clusterId)}; font-weight:bold;'>■</span> Cluster ID: ${d.clusterId}</div>`;
            }
            if (d.fx != null) content += `<div><small style='color:#d97706;'>⚠️ Position Pinned</small></div>`;
            tooltip.style("opacity", 1)
                .html(content)
                .style("left", (event.pageX + 10) + "px")
                .style("top", (event.pageY + 10) + "px");
        })
        .on("mouseout", () => tooltip.style("opacity", 0))
        .on("click", function(event, d) {
            event.stopPropagation();
            nodeLayer.selectAll(".node").classed("selected", false);
            d3.select(this).classed("selected", true);
            updateSidebar(d);
        });

    node.append("path")
        .attr("class", "node-shape")
        .attr("d", d => symbolGenerator.type(getShapeForCluster(d.clusterId)).size(getSizeForNode(d))())
        .style("fill", d => d.clusterId ? clusterColors(d.clusterId) : "#ff3b30");

    node.append("text")
        .attr("dx", d => d.count ? Math.max(12, Math.sqrt(d.count) * 4 + 6) : 15)
        .attr("dy", 4)
        .text(d => d.label);

    // Canvas background selection clearance listeners
    svg.on("click", () => {
        g.selectAll(".node").classed("selected", false);
        d3.select("#cluster-bubbles").selectAll("path").classed("selected-bubble", false).attr("stroke-width", 2);
        
        d3.select("#node-sidebar")
            .classed("empty", true)
            .html('<div class="text-gray-400 text-sm text-center">Click on an isolate node or cluster bubble to view details</div>');
    });

    // --- Dynamic Layout Ticking Loops ---
    let tickCount = 0;
    const maxTicks = 250;

    globalSimulation.on("tick", () => {
        link.attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);

        node.attr("transform", d => `translate(${d.x},${d.y})`);

        edgeLabelGroups.attr("transform", d => `translate(${(d.source.x + d.target.x) / 2},${(d.source.y + d.target.y) / 2})`);

        // Redraw hulls inside the active tick pipeline loop
        updateClusterBubbles();

        if (++tickCount >= maxTicks) {
            globalSimulation.stop();
            tickCount = 0; 
            if (isInitialLoad) {
                fitToView();
                isInitialLoad = false;
            }
        }
    });

    d3.select("#cluster-algorithm").on("change", function() {
        computeEpidemicClusters(this.value);
    });
}


// ===================================
// --- Epidemic Clustering Engine ---
// ===================================

/**
 * Implements Breadth-First Search (BFS) graph partitioning over the visualization setup.
 * Breaks down target components and assigns distinct tracking shapes and color metrics
 * depending on selected epidemiologic distance cutoffs.
 */
function computeEpidemicClusters(mode) {
    const nodeLayer = d3.select("#nodes-group");

    // NEW: Reset the sidebar view back to a neutral fallback prompt on algorithm switch
    d3.select("#node-sidebar")
        .classed("empty", true)
        .html('<div class="text-gray-400 text-sm text-center">Click on an isolate node or cluster bubble to view details</div>');

    // Remove border stroke highlights from any previously selected cluster hulls
    d3.select("#cluster-bubbles").selectAll("path")
        .classed("selected-bubble", false)
        .attr("stroke-width", 2);

    if (mode === "none") {
        // Reset nodes back to their default standalone styling uniform state
        nodeLayer.selectAll(".node-shape")
            .transition()
            .duration(300)
            .attr("d", d => symbolGenerator.type(d3.symbolCircle).size(getSizeForNode(d))())
            .style("fill", "#ff3b30");

        g.selectAll("line").classed("dimmed", false);
        globalNodes.forEach(n => n.clusterId = null);
        
        updateClusterBubbles(); // Erases paths smoothly
        globalSimulation.alpha(0.2).restart();
        return;
    }

    // Build the adjacency mapping matrix for the graph partitioning
    const nodeMap = new Map(globalNodes.map(n => [n.id, n]));
    const adj = {};
    globalNodes.forEach(n => adj[n.id] = []);

    globalLinks.forEach(l => {
        const sId = l.source.id;
        const tId = l.target.id;
        
        // Match link metrics against active cutoffs
        let keepEdge = (mode === "cc" && l.weight <= 2) || 
                       (mode === "gr" && l.weight <= 10) ||
                       (mode === "components");

        if (keepEdge) {
            adj[sId]?.push(tId);
            adj[tId]?.push(sId);
        }
    });

    const visited = new Set();
    let currentClusterNum = 0;

    // Execute Breadth-First Search (BFS) to segment our population partitions
    globalNodes.forEach(n => {
        if (!visited.has(n.id)) {
            currentClusterNum++;
            const queue = [n.id];
            visited.add(n.id);

            while (queue.length > 0) {
                const curr = queue.shift();
                const targetNode = nodeMap.get(curr);
                if (targetNode) targetNode.clusterId = currentClusterNum;

                const neighbors = adj[curr] || [];
                for (let i = 0; i < neighbors.length; i++) {
                    const neighbor = neighbors[i];
                    if (!visited.has(neighbor)) {
                        visited.add(neighbor);
                        queue.push(neighbor);
                    }
                }
            }
        }
    });

    nodeLayer.selectAll(".node-shape")
        .transition()
        .duration(300)
        .attr("d", d => symbolGenerator.type(getShapeForCluster(d.clusterId)).size(getSizeForNode(d))())
        .style("fill", d => d.clusterId ? clusterColors(d.clusterId) : "#ff3b30");

    // Fade out connection links that do not fall within the active partition criteria bounds
    g.selectAll("line").classed("dimmed", d => {
        if (mode === "cc" && d.weight > 2) return true;
        if (mode === "gr" && d.weight > 10) return true;
        return false;
    });

    // Update the hulls immediately with the freshly assigned cluster identifiers
    updateClusterBubbles();

    // Kick the physics processing loop back into gear to pull group elements together
    globalSimulation.alpha(0.4).restart();
}
/**
 * Computes Convex Hull bounding areas for active cluster identifiers, 
 * pads coordinates, and renders smooth background paths with interaction hooks and cluster dragging.
 */
function updateClusterBubbles() {
    const bubbleLayer = d3.select("#cluster-bubbles");
    
    // Group all current visualization nodes by assigned cluster IDs
    const groups = {};
    globalNodes.forEach(n => {
        if (n.clusterId !== undefined && n.clusterId !== null) {
            if (!groups[n.clusterId]) groups[n.clusterId] = [];
            groups[n.clusterId].push(n);
        }
    });

    const bubbleData = [];

    Object.entries(groups).forEach(([clusterId, nodes]) => {
        // Skip single independent isolates (no bubble boundary needed)
        if (nodes.length < 2) return;

        let points = [];
        
        // Generate a padded artificial coordinate boundary footprint around data targets
        nodes.forEach(n => {
            const offset = 22; // Matches collision margins closely
            points.push([n.x - offset, n.y - offset]);
            points.push([n.x + offset, n.y - offset]);
            points.push([n.x - offset, n.y + offset]);
            points.push([n.x + offset, n.y + offset]);
        });

        // Compute minimal enclosing convex geometry polygon path
        const hull = d3.polygonHull(points);
        if (hull) {
            bubbleData.push({
                clusterId: clusterId,
                nodes: nodes, // Reference to all nodes in this cluster
                pathData: d3.line().curve(d3.curveBasisClosed)(hull)
            });
        }
    });

    // D3 Data binding routine injecting computed geometries onto structural path tags
    const bubbles = bubbleLayer.selectAll("path")
        .data(bubbleData, d => d.clusterId);

    bubbles.exit().remove();

    bubbles.enter()
        .append("path")
        .attr("class", "cluster-bubble-hull")
        .attr("fill-opacity", 0.15)
        .attr("stroke-width", 2)
        .attr("stroke-linejoin", "round")
        .style("cursor", "grab")
        // Pointer events config to allow hover/click effects on the layout cleanly
        .style("pointer-events", "visiblePainted") 
        .on("mouseover", function() {
            d3.select(this).transition().duration(150).attr("fill-opacity", 0.3);
        })
        .on("mouseout", function() {
            d3.select(this).transition().duration(150).attr("fill-opacity", 0.15);
        })
        .on("click", function(event, d) {
            event.stopPropagation();
            
            // Highlight this bubble and dim others
            bubbleLayer.selectAll("path").classed("selected-bubble", false).attr("stroke-width", 2);
            d3.select(this).classed("selected-bubble", true).attr("stroke-width", 4);
            
            // Clear individual node selections
            d3.selectAll(".node").classed("selected", false);
            
            // Trigger the sidebar overview panel
            updateSidebarWithCluster(d.clusterId, d.nodes);
        })
        // INJECT CLUSTER DRAG BEHAVIOR
        .call(dragCluster(globalSimulation))
        .merge(bubbles)
        .attr("d", d => d.pathData)
        .attr("fill", d => clusterColors(d.clusterId))
        .attr("stroke", d => d.clusterId ? clusterColors(d.clusterId) : "transparent");
}


// =============================
// --- Cluster Drag Mechanics ---
// =============================

/**
 * Handles group-dragging dynamics for full cluster bubble complexes.
 * Moves every internal isolate simultaneously relative to mouse translation offsets.
 */
function dragCluster(simulation) {
    return d3.drag()
        .on("start", function(event, d) {
            if (!event.active) simulation.alphaTarget(0.1).restart();
            d3.select(this).style("cursor", "grabbing");
            
            // Cache starting positions of all elements inside the cluster
            d.nodes.forEach(n => {
                n.baseX = n.x;
                n.baseY = n.y;
            });
        })
        .on("drag", function(event, d) {
            // event.dx and event.dy provide relative coordinate mutations per tick frame
            d.nodes.forEach(n => {
                n.x += event.dx;
                n.y += event.dy;
                // Fix coordinates so the nodes don't rubber-band back to gravity wells while dragging
                n.fx = n.x;
                n.fy = n.y;
            });
        })
        .on("end", function(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            d3.select(this).style("cursor", "grab");
            
            // Pin the entire cluster in its new position layout space
            d.nodes.forEach(n => {
                n.fx = n.x;
                n.fy = n.y;
            });
            
            // Mark corresponding DOM elements visually as pinned
            d3.selectAll(".node")
                .filter(n => n.clusterId === d.clusterId)
                .classed("pinned", true);
                
            simulation.alpha(0.1).restart();
        });
}

// ===================================
// --- View Fitting & UI Mutators ---
// ===================================

/**
 * Automatically adjusts zoom matrix states to bound graph layouts centrally
 * inside the available viewing window without clipping node edges.
 */
function fitToView() {
    if (!globalNodes || globalNodes.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    
    // Scan current coordinates to identify graph layout extents
    globalNodes.forEach(node => {
        minX = Math.min(minX, node.x);
        minY = Math.min(minY, node.y);
        maxX = Math.max(maxX, node.x);
        maxY = Math.max(maxY, node.y);
    });

    if (!isFinite(minX) || !isFinite(minY)) return;

    const nodeWidth = maxX - minX;
    const nodeHeight = maxY - minY;
    
    // Safe buffer zone padding around data elements so bubble perimeters aren't cropped
    const padding = 80; 

    // Calculate optimal scale vector tracking dimensional limits
    let scale = Math.min(
        (width - 2 * padding) / (nodeWidth || 1),
        (height - 2 * padding) / (nodeHeight || 1)
    );
    
    // Set absolute scaling limits for standard viewport fittings
    scale = Math.max(0.15, Math.min(scale, 1.2));

    // Derive correct centering translation vectors
    const tx = width / 2 - (minX + nodeWidth / 2) * scale;
    const ty = height / 2 - (minY + nodeHeight / 2) * scale;

    // Apply smooth visual shift transition directly onto the zoom target element instance
    svg.transition()
        .duration(500)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

/**
 * Renders complete contextual profiles onto DOM components when node objects are toggled.
 */
function updateSidebar(node) {
    const sidebar = d3.select("#node-sidebar").classed("empty", false).html("");
    
    // Title/Isolate Label
    sidebar.append("div")
        .attr("class", "text-lg font-bold text-gray-900 mb-3")
        .text(`Isolate: ${node.label}`);
    
    // Sampling Metadata block
    sidebar.append("div")
        .attr("class", "mb-4")
        .html(`
            <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Sampling Metadata</div>
            <div class="text-sm text-gray-700 break-words">Location: ${node.sampling_location || 'N/A'}</div>
            <div class="text-sm text-gray-700 break-words">Date: ${node.sampling_date || 'N/A'}</div>
        `);

    // Assembly Metrics Block
    sidebar.append("div")
        .attr("class", "mb-4")
        .html(`
            <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Assembly Metrics</div>
            <div class="bg-gray-100/60 border border-gray-200/80 rounded-md p-2.5 flex flex-col gap-1.5 text-xs text-gray-700">
                <div class="flex justify-between">
                    <span class="text-gray-500 font-medium">Contig Count:</span>
                    <span class="font-bold text-gray-900">${node.contig_count || 0}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-500 font-medium">N50 Value:</span>
                    <span class="font-bold text-gray-900">${formatGenomicSize(node.n50)}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-500 font-medium">Assembly Size:</span>
                    <span class="font-bold text-gray-900">${formatGenomicSize(node.assembly_size)}</span>
                </div>
            </div>
        `);
    
    // MLVA Profile Loci Section
    const profileSection = sidebar.append("div").attr("class", "mb-4");
    profileSection.append("div")
        .attr("class", "text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2")
        .text("MLVA Loci Profile");
        
    if (node.locus_results && Object.keys(node.locus_results).length > 0) {
        const locusContainer = profileSection.append("div")
            .attr("class", "grid grid-cols-1 gap-1 max-h-64 overflow-y-auto pr-1");
        
        Object.entries(node.locus_results)
            .sort((a, b) => a[0].localeCompare(b[0]))
            .forEach(([locusName, repeatCount]) => {
                locusContainer.append("div")
                    .attr("class", "flex justify-between items-center p-2 bg-white border border-gray-200 rounded text-sm shadow-sm")
                    .html(`
                        <span class="font-medium text-gray-700">${locusName}</span>
                        <span class="px-2 py-0.5 text-xs font-bold bg-blue-100 text-blue-800 rounded-full">${repeatCount}</span>
                    `);
            });
    } else {
        profileSection.append("div")
            .attr("class", "text-sm text-gray-400 italic bg-gray-100 p-2 rounded text-center")
            .text("No locus results available");
    }
}

/**
 * Renders an aggregate profile overview of all isolates matching a clicked cluster grouping,
 * including visual quality summaries and an interactive locus metrics matrix.
 */
function updateSidebarWithCluster(clusterId, nodes) {
    const sidebar = d3.select("#node-sidebar").classed("empty", false).html("");
    
    // Header section
    sidebar.append("div")
        .attr("class", "text-lg font-bold text-gray-900 mb-1 flex items-center gap-2")
        .html(`<span style='color:${clusterColors(clusterId)}'>■</span> Cluster ${clusterId} Overview`);
        
    sidebar.append("div")
        .attr("class", "text-xs font-semibold text-gray-500 uppercase tracking-wide mb-4")
        .text(`${nodes.length} Connected Isolate Groups`);

    // Dynamic scroll container for cluster isolates list
    const listContainer = sidebar.append("div")
        .attr("class", "flex-1 overflow-y-auto pr-1 flex flex-col gap-3");

    // Loop through each node within the hull grouping block
    nodes.sort((a, b) => a.label.localeCompare(b.label)).forEach(node => {
        const itemBox = listContainer.append("div")
            .attr("class", "p-3 bg-white border border-gray-200 rounded-lg shadow-sm hover:border-blue-400 hover:shadow transition-all cursor-pointer")
            .on("click", (event) => {
                event.stopPropagation();
                g.selectAll(".node").classed("selected", false);
                g.selectAll(".node").filter(n => n.id === node.id).classed("selected", true);
                updateSidebar(node);
            });

        // Top Row: Isolate Name & Core Counts
        const topRow = itemBox.append("div").attr("class", "flex justify-between items-start mb-1");
        topRow.append("div")
            .attr("class", "font-bold text-sm text-gray-800 break-all pr-2")
            .text(node.label);

        // Sub-Row: Location/Metadata text
        if (node.sampling_location || node.sampling_date) {
            let metaParts = [];
            if (node.sampling_location) metaParts.push(node.sampling_location);
            if (node.sampling_date) metaParts.push(node.sampling_date);
            itemBox.append("div")
                .attr("class", "text-xs text-gray-400 mb-1.5 truncate")
                .text(metaParts.join(" | "));
        }

        // Expanded Inline Section: Loci Badge Block Grid
        if (node.locus_results && Object.keys(node.locus_results).length > 0) {
            const locusStrip = itemBox.append("div")
                .attr("class", "flex flex-wrap gap-1 mt-2 pt-2 border-t border-gray-100");

            Object.entries(node.locus_results)
                .sort((a, b) => a[0].localeCompare(b[0]))
                .forEach(([locusName, repeatCount]) => {
                    locusStrip.append("div")
                        .attr("class", "text-[11px] overflow-hidden")
                        .html(`
                            <div class="border-[1px] p-[2.5px] border-slate-200 text-center">
                                <div class="text-slate-500 font-medium">${locusName}</div>
                                <div class="font-bold text-slate-800">${repeatCount}</div>
                            </div>
                        `);
                });
        } else {
            itemBox.append("div")
                .attr("class", "text-[11px] text-gray-400 italic mt-1")
                .text("No locus profile loaded");
        }
    });
}


// =======================
// --- Drag Mechanics ---
// =======================

/**
 * Initializes cursor drag interactions, locking simulation parameters safely
 * to allow smooth placement adjustments without layout rubber-banding.
 */
function drag(simulation) {
    return d3.drag()
        .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.1).restart();
            d.fx = event.x; d.fy = event.y;
        })
        .on("drag", (event, d) => {
            d.fx = event.x; d.fy = event.y;
        })
        .on("end", function(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            simulation.alpha(0.1).restart(); 
            d.fx = event.x; d.fy = event.y;
            d3.select(this).classed("pinned", true);
        });
}

// ===================================
// --- Utility Security Hashing ---
// ===================================

/**
 * Generates an alphanumeric hash string mapping unique ID variables of excluded items.
 * Used to catch and apply state changes across multi-tab configurations.
 */
function computeChecksum(isolates) {
    if (!isolates || isolates.length === 0) return "empty";
    const sortedIds = isolates.map(iso => iso.id).sort().join(",");
    let hash = 0;
    for (let i = 0; i < sortedIds.length; i++) {
        hash = ((hash << 5) - hash) + sortedIds.charCodeAt(i);
        hash |= 0; // Forces absolute 32bit integer compliance bounds safely
    }
    return hash.toString(36);
}

// ===================================
// --- Helper Functions ---
// ===================================

/**
 * Formats a raw base-pair value into the most readable genomic unit (Gb, Mb, kb, or bp).
 * Supports standard scientific truncation formats.
 */
function formatGenomicSize(bp) {
    if (bp === undefined || bp === null || isNaN(bp) || bp === 0) return 'N/A';
    
    if (bp >= 1e9) {
        return (bp / 1e9).toFixed(2) + ' Gb';
    } else if (bp >= 1e6) {
        return (bp / 1e6).toFixed(2) + ' Mb';
    } else if (bp >= 1e3) {
        return (bp / 1e3).toFixed(1) + ' kb';
    }
    return bp.toLocaleString() + ' bp';
}