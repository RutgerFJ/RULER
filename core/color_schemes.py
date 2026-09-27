"""
Color schemes for MLVA visualization.
Each scheme defines a gradient from low values (0.0) to high values (1.0).
"""

# Color schemes: each is a list of (normalized_position, rgb_tuple) for interpolation
COLOR_SCHEMES = {
    'default': {
        'name': 'Blue to Red',
        'description': 'Blue → Cyan → Yellow → Orange → Red',
        'colors': [
            (0.0, (3, 105, 161)),        # Blue
            (0.25, (6, 182, 212)),       # Cyan
            (0.5, (234, 179, 8)),        # Yellow
            (0.75, (249, 115, 22)),      # Orange
            (1.0, (220, 38, 38)),        # Red
        ]
    },
    'viridis': {
        'name': 'Viridis',
        'description': 'Purple → Blue → Green → Yellow',
        'colors': [
            (0.0, (68, 1, 84)),          # Purple
            (0.25, (59, 82, 139)),       # Blue-purple
            (0.5, (33, 145, 140)),       # Teal
            (0.75, (253, 231, 37)),      # Yellow-green
            (1.0, (255, 231, 0)),        # Yellow
        ]
    },
    'plasma': {
        'name': 'Plasma',
        'description': 'Purple → Red → Yellow',
        'colors': [
            (0.0, (13, 8, 135)),         # Purple
            (0.25, (126, 3, 168)),       # Magenta
            (0.5, (230, 72, 33)),        # Red-orange
            (0.75, (253, 183, 23)),      # Orange-yellow
            (1.0, (240, 251, 15)),       # Yellow
        ]
    },
    'cool': {
        'name': 'Cool',
        'description': 'Light Blue → Dark Blue',
        'colors': [
            (0.0, (173, 216, 230)),      # Light blue
            (0.25, (135, 206, 235)),     # Sky blue
            (0.5, (65, 105, 225)),       # Royal blue
            (0.75, (25, 25, 112)),       # Midnight blue
            (1.0, (0, 0, 51)),           # Very dark blue
        ]
    },
    'warm': {
        'name': 'Warm',
        'description': 'Light yellow → Deep red',
        'colors': [
            (0.0, (255, 255, 200)),      # Light yellow
            (0.25, (255, 200, 100)),     # Light orange
            (0.5, (255, 140, 0)),        # Orange
            (0.75, (220, 80, 0)),        # Dark orange
            (1.0, (139, 0, 0)),          # Dark red
        ]
    },
    'grayscale': {
        'name': 'Grayscale',
        'description': 'White → Black',
        'colors': [
            (0.0, (255, 255, 255)),      # White
            (0.5, (128, 128, 128)),      # Gray
            (1.0, (0, 0, 0)),            # Black
        ]
    },
}


def interpolate_color(normalized_value, colors):
    """
    Interpolate between colors based on normalized value (0.0 to 1.0).
    
    Args:
        normalized_value: Float between 0.0 and 1.0
        colors: List of (position, (r, g, b)) tuples defining the gradient
        
    Returns:
        Tuple of (r, g, b) integers
    """
    normalized_value = max(0.0, min(1.0, normalized_value))
    
    # Find the two colors to interpolate between
    for i in range(len(colors) - 1):
        pos1, color1 = colors[i]
        pos2, color2 = colors[i + 1]
        
        if pos1 <= normalized_value <= pos2:
            # Interpolate between these two colors
            if pos2 == pos1:
                # Avoid division by zero
                return color1
            
            ratio = (normalized_value - pos1) / (pos2 - pos1)
            r = int(color1[0] + (color2[0] - color1[0]) * ratio)
            g = int(color1[1] + (color2[1] - color1[1]) * ratio)
            b = int(color1[2] + (color2[2] - color1[2]) * ratio)
            return (r, g, b)
    
    # Return last color if normalized_value is at the end
    return colors[-1][1]


def get_color_for_value(value, min_value, max_value, scheme='default'):
    """
    Get RGB color for a normalized value using the specified scheme.
    
    Args:
        value: The actual value to color
        min_value: Minimum value in the range
        max_value: Maximum value in the range
        scheme: Name of the color scheme to use
        
    Returns:
        String in format 'rgb(r, g, b)'
    """
    if scheme not in COLOR_SCHEMES:
        scheme = 'default'
    
    scheme_colors = COLOR_SCHEMES[scheme]['colors']
    
    # Normalize value to 0-1 range
    if max_value == min_value:
        normalized = 0.5
    else:
        normalized = (value - min_value) / (max_value - min_value)
    
    r, g, b = interpolate_color(normalized, scheme_colors)
    return f'rgb({r}, {g}, {b})'


def get_available_schemes():
    """Return a list of available color schemes."""
    return [
        {
            'id': key,
            'name': scheme['name'],
            'description': scheme['description']
        }
        for key, scheme in COLOR_SCHEMES.items()
    ]


def get_gradient_css(scheme='default'):
    """
    Generate a CSS linear gradient string from a color scheme.
    
    Args:
        scheme: Name of the color scheme to use
        
    Returns:
        CSS linear-gradient string (e.g., "linear-gradient(to right, rgb(...), rgb(...), ...)")
    """
    if scheme not in COLOR_SCHEMES:
        scheme = 'default'
    
    colors = COLOR_SCHEMES[scheme]['colors']
    
    # Build gradient stops
    stops = []
    for position, (r, g, b) in colors:
        percentage = int(position * 100)
        stops.append(f'rgb({r}, {g}, {b}) {percentage}%')
    
    return f"linear-gradient(to right, {', '.join(stops)})"
