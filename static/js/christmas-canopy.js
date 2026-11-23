(() => {
    'use strict';
    const margin = 60;
    const maxPixels = 420000;
    const maxBulbs = 36;
    const vertex = `#version 300 es
        void main() {
            vec2 vertex = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
            gl_Position = vec4(vertex * 2. - 1., 0., 1.);
        }`;
    const fragment = `#version 300 es
        precision highp float;
        precision highp int;
        out vec4 frag;
        const float BULB_IOR = 1.51, GLASS_IOR = 1.46, MISS = 1e5;
        uniform vec2 u_pixel;
        uniform vec4 u_bounds, u_bar;
        uniform float u_radius, u_scale, u_day;
        uniform int u_count;
        uniform vec4 u_bulbs[${maxBulbs}], u_lamps[${maxBulbs}];
        uniform vec3 u_ripple;

        float sq(float x) { return x*x; }
        float rim(vec2 p) {
            vec2 q = abs(p - u_bar.zw*.5) - u_bar.zw*.5 + u_radius;
            return length(max(q, 0.)) + min(max(q.x,q.y), 0.) - u_radius;
        }
        vec2 rimNormal(vec2 p) {
            vec2 center = p - u_bar.zw*.5;
            vec2 q = abs(center) - u_bar.zw*.5 + u_radius;
            vec2 r = max(q, 0.);
            return (dot(r,r) > .0001 ? normalize(r) : (q.x > q.y ? vec2(1,0) : vec2(0,1))) * sign(center);
        }
        float profile(float d) {
            return sqrt(max(.12, 100. - sq(clamp(10. + d, 0., 10.))));
        }
        float ripple(vec2 p, out vec2 slope) {
            slope = vec2(0);
            if (u_ripple.z < 0.) return 0.;
            vec2 delta = p - u_ripple.xy;
            float dist = length(delta), crest = dist - 140. * u_ripple.z;
            float wave = 2.7 * exp(-3.3*u_ripple.z - sq(crest/22.)) * smoothstep(0.,7.,dist);
            slope = delta / max(1.,dist) * wave * (-.25*sin(crest*.25) - 2.*crest/484.*cos(crest*.25));
            return wave * cos(crest*.25);
        }
        vec3 localRay(vec3 r, vec2 axis) {
            return vec3(dot(r.xy, vec2(axis.y, -axis.x)), dot(r.xy, axis), r.z);
        }
        float hitEllipsoid(vec3 ro, vec3 rd, vec3 radii) {
            vec3 o = ro/radii, d = rd/radii;
            float a = dot(d,d), b = dot(o,d), c = dot(o,o)-1.;
            float h = b*b-a*c;
            if (h < 0.) return MISS;
            float hit = (-b-sqrt(h))/a;
            return hit > .02 ? hit : MISS;
        }
        float hitCap(vec3 ro, vec3 rd) {
            float radius = 2.3*u_scale, halfLength = 2.5*u_scale;
            float a = dot(rd.xz,rd.xz), b = dot(ro.xz,rd.xz), c = dot(ro.xz,ro.xz)-radius*radius;
            float discriminant = b*b - a*c, hit = MISS;
            if (a > .00001 && discriminant >= 0.) {
                float t = (-b-sqrt(discriminant))/a;
                if (t > .02 && abs(ro.y+rd.y*t) < halfLength) hit = t;
            }
            if (abs(rd.y) > .00001) {
                float t = (sign(-rd.y)*halfLength-ro.y)/rd.y;
                if (t > .02 && dot((ro+rd*t).xz,(ro+rd*t).xz) < radius*radius) hit = min(hit,t);
            }
            return hit;
        }
        vec3 studio(vec3 direction) {
            float key = pow(max(0., dot(direction,normalize(vec3(-.41,-.5,.76)))),14.);
            float strip = exp(-sq((direction.y+.36)*6.)) * smoothstep(-.5,.16,direction.z);
            float fill = pow(max(0., dot(direction,normalize(vec3(.59,.25,.76)))),24.);
            return vec3(.10,.17,.16)*strip + vec3(.91,.83,.66)*key + vec3(.41,.61,.78)*fill;
        }
        vec3 shadeBulb(vec3 origin, vec3 ray, float t, vec3 radii, vec4 lamp) {
            vec3 point = origin+ray*t, normal = normalize(point/(radii*radii));
            vec3 through = refract(ray,normal,1./BULB_IOR), reflection = reflect(ray,normal);
            float fresnel = .042 + .958*pow(1. - clamp(dot(normal,-ray),0.,1.),5.);
            float tFilament = -point.z/min(-.035,through.z);
            vec2 fil = (point+through*tFilament).xy/u_scale;
            float coil = exp(-sq((fil.x-.69*sin(fil.y*4.2))*1.62));
            float filament = coil*smoothstep(-4.,-2.8,fil.y)*(1.-smoothstep(3.1,5.,fil.y))*step(0.,tFilament);
            float spread = exp(-dot(fil/vec2(3.3,6.6),fil/vec2(3.3,6.6)));
            float glint = pow(max(0.,dot(reflection,normalize(vec3(-.5,-.25,.83)))),39.);
            vec3 tint = lamp.rgb*(.15 + .15*normal.z + .46*spread*lamp.a);
            vec3 fire = (mix(lamp.rgb,vec3(1.,.77,.5),.38)*1.8*filament + lamp.rgb*.70*spread)*lamp.a;
            return tint*(1.-fresnel*.6) + studio(reflection)*(.24+fresnel*.50) + vec3(.98,.93,.82)*glint*.84 + fire;
        }
        vec3 displayColor(vec3 linearLight) {
            return pow(1.-exp(-max(vec3(0),linearLight)*1.35),vec3(1./2.2));
        }
        void main() {
            vec2 p = u_bounds.xy + vec2(gl_FragCoord.x*u_pixel.x, u_bounds.w-gl_FragCoord.y*u_pixel.y);
            float d = rim(p);
            // Interaction and colored spill are confined to the bezel, not composited from the live DOM.
            if (d > 53. || d < -70.) { frag=vec4(0); return; }
            float aa = max(u_pixel.x,u_pixel.y)*.8;
            float inside = 1. - smoothstep(-aa,aa,d);
            bool glass = inside > 0. && d > -67.;
            vec3 light = vec3(0), object = vec3(0), mirrored = vec3(0);
            float best = MISS, mirrorHit = MISS, fresnel = 0., ring = 0., sheen = 0., transmission = 1.;
            vec3 ray = vec3(0,0,-1), view = vec3(p,95.);
            vec3 glassHit=vec3(0), normal=vec3(0,0,1), reflected=vec3(0,0,1), exitRay=ray;
            if (glass) {
                vec2 slope;
                ring = ripple(p,slope);
                float z = profile(d), edge = clamp(10.+d,0.,10.);
                vec2 gradient = -rimNormal(p)*(edge/max(.72,z)) + slope;
                gradient.y += .14*(1.-2.*p.y/u_bar.w);
                normal = normalize(vec3(-gradient,1.));
                glassHit = vec3(p,3.+z);
                reflected = reflect(ray,normal);
                fresnel = .035+.965*pow(1.-normal.z,5.);
                vec3 bent = refract(ray,normal,1./GLASS_IOR), back = glassHit;
                for (int j=0; j<2; j++) back = glassHit + bent*((glassHit.z+3.+.5*profile(rim(back.xy)))/max(.25,-bent.z));
                float backDist = rim(back.xy);
                vec2 rearSlope = rimNormal(back.xy)*(.5*clamp(10.+backDist,0.,10.)/max(.7,profile(backDist)));
                exitRay = refract(bent,normalize(vec3(-rearSlope,1.)),GLASS_IOR);
                transmission = step(.01,dot(exitRay,exitRay));
                sheen = exp(-sq((d+1.8)/1.2)) * (.33+.67*max(0.,-rimNormal(p).y));
            }
            vec3 radii = vec3(4.25,7.3,4.6)*u_scale;
            for (int i=0; i<u_count; i++) {
                vec4 bulb = u_bulbs[i], lamp = u_lamps[i];
                vec2 offset = p-bulb.xy;
                if (any(greaterThan(abs(offset),vec2(82.)))) continue;
                vec3 center = vec3(bulb.xy+bulb.zw*11.7*u_scale,26.);
                vec2 fromBulb = p-center.xy;
                float dist = dot(fromBulb,fromBulb);
                light += lamp.rgb*lamp.a*(.25*exp(-dist/65.)+.115*exp(-dist/265.))*(1.-smoothstep(1600.,2400.,dist));
                if (glass) {
                    vec3 delta = center-glassHit;
                    float dist2 = dot(delta,delta);
                    if (dist2 < 4800.) {
                        vec3 to = delta*inversesqrt(max(1.,dist2));
                        float facing = max(0.,dot(normal,to));
                        float glint = pow(max(0.,dot(normal,normalize(to+vec3(0,0,1)))),26.);
                        float refracted = max(0.,dot(-to,exitRay))*transmission;
                        light += lamp.rgb*lamp.a*inside*(facing*.19 + glint*.60 + refracted*.07)
                            * (340./(dist2+105.))*(1.-smoothstep(3000.,4800.,dist2));
                        if (d > -14. && reflected.z > .17 && dist2 < 2400.) {
                            vec3 origin = localRay(glassHit-center,bulb.zw), direction = localRay(reflected,bulb.zw);
                            float t = hitEllipsoid(origin,direction,radii);
                            if (t < mirrorHit) {
                                mirrorHit = t;
                                mirrored = shadeBulb(origin,direction,t,radii,lamp) * (.13 + .65*fresnel);
                            }
                        }
                    }
                }
                if (all(lessThanEqual(abs(fromBulb),vec2(13.)*u_scale))) {
                    vec3 origin = localRay(view-center,bulb.zw);
                    float t = hitEllipsoid(origin,ray,radii);
                    if (t < best) { best=t; object=shadeBulb(origin,ray,t,radii,lamp); }
                }
                if (all(lessThanEqual(abs(offset),vec2(7.)*u_scale))) {
                    vec3 cap = localRay(view-vec3(bulb.xy+bulb.zw*2.5*u_scale,26.),bulb.zw);
                    float t = hitCap(cap,ray);
                    if (t < best) {
                        best=t;
                        vec3 q = cap+ray*t;
                        float threads=.69+.31*cos(q.y*6.1/u_scale);
                        object=vec3(.021,.063,.044)*(threads+.48*q.z/(2.3*u_scale))
                            + vec3(.13,.13,.093)*exp(-sq((q.x/u_scale+1.05)/.84))*threads;
                    }
                }
            }
            if (best < MISS) { frag = vec4(displayColor(object+light*.08),1.); return; }
            light += mirrored*inside + inside*(vec3(.15,.25,.32)*sheen + vec3(.10,.24,.27)*abs(ring)*.21);
            light *= mix(1.,.71,u_day);
            vec3 lit = displayColor(light);
            float peak = max(lit.r,max(lit.g,lit.b));
            float alpha = min(.76,peak*.68)*smoothstep(.008,.048,peak);
            frag = vec4(lit/max(.025,peak)*alpha,alpha);
        }`;

    function compile(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
        const message = gl.getShaderInfoLog(shader);
        gl.deleteShader(shader);
        throw new Error(message);
    }

    window.requestHubChristmasCanopy = function createCanopy(onLoss) {
        const canvas = document.createElement('canvas');
        canvas.className = 'seasonal-canopy-rays';
        canvas.setAttribute('aria-hidden', 'true');
        const gl = canvas.getContext('webgl2', {
            alpha: true, premultipliedAlpha: true, depth: false, stencil: false, antialias: true,
            powerPreference: 'low-power', failIfMajorPerformanceCaveat: true,
        });
        if (!gl) return null;
        const program = gl.createProgram();
        const shaders = [];
        try {
            shaders.push(compile(gl, gl.VERTEX_SHADER, vertex));
            shaders.push(compile(gl, gl.FRAGMENT_SHADER, fragment));
            shaders.forEach(shader => gl.attachShader(program, shader));
            gl.linkProgram(program);
            if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
            gl.useProgram(program);
        } catch (error) {
            gl.deleteProgram(program);
            gl.getExtension('WEBGL_lose_context')?.loseContext();
            throw error;
        } finally {
            shaders.forEach(shader => gl.deleteShader(shader));
        }
        const names = ['pixel', 'bounds', 'bar', 'radius', 'scale', 'day', 'count', 'bulbs[0]', 'lamps[0]', 'ripple'];
        const uniform = Object.fromEntries(names.map(name => [name, gl.getUniformLocation(program, `u_${name}`)]));
        const bulbs = new Float32Array(maxBulbs * 4), lamps = new Float32Array(maxBulbs * 4);
        const textureLimit = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE);
        let width, height, left, top;
        const linearColors = new Map();
        canvas.addEventListener('webglcontextlost', onLoss);
        return {
            canvas,
            draw(scene, viewportLeft, day, ripple, pixelBudget = maxPixels) {
                if (gl.isContextLost()) return false;
                // The canvas and SVG share the surface's padding-box origin.
                const x = Math.max(-margin, Math.ceil(-viewportLeft));
                const y = -margin;
                const right = Math.min(scene.width + margin, Math.floor(document.documentElement.clientWidth - viewportLeft));
                const w = right - x;
                const h = scene.height + margin * 2;
                const ratio = Math.min(window.devicePixelRatio || 1, 1.5, Math.sqrt(pixelBudget / (w*h)), textureLimit / Math.max(w,h));
                const pxWidth = Math.max(1, Math.floor(w * ratio)), pxHeight = Math.max(1, Math.floor(h * ratio));
                if (w !== width || h !== height || x !== left || y !== top) {
                    width = w;
                    height = h;
                    left = x;
                    top = y;
                    Object.assign(canvas.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
                }
                if (canvas.width !== pxWidth || canvas.height !== pxHeight) {
                    canvas.width = pxWidth;
                    canvas.height = pxHeight;
                }
                gl.viewport(0,0,pxWidth,pxHeight);
                gl.uniform2f(uniform.pixel,w/pxWidth,h/pxHeight);
                gl.uniform4f(uniform.bounds,x,y,w,h);
                gl.uniform4f(uniform.bar,0,0,scene.width,scene.height);
                gl.uniform1f(uniform.radius,scene.radius);
                gl.uniform1f(uniform.scale,scene.scale);
                gl.uniform1f(uniform.day,Number(day));
                gl.uniform1i(uniform.count,scene.bulbs.length);
                scene.bulbs.forEach(({position, color, emission},i) => {
                    const angle = position.angle*Math.PI/180, j=i*4;
                    bulbs[j]=position.x; bulbs[j+1]=position.y; bulbs[j+2]=Math.sin(angle); bulbs[j+3]=-Math.cos(angle);
                    if (!linearColors.has(color)) linearColors.set(color, color.slice(1).match(/../g)
                        .map(hex => ((parseInt(hex,16)/255+.055)/1.055)**2.4));
                    lamps.set(linearColors.get(color),j);
                    lamps[j+3]=emission;
                });
                gl.uniform4fv(uniform['bulbs[0]'],bulbs);
                gl.uniform4fv(uniform['lamps[0]'],lamps);
                gl.uniform3f(uniform.ripple,ripple?.x ?? 0,ripple?.y ?? 0,ripple?.age ?? -1);
                gl.drawArrays(gl.TRIANGLES,0,3);
                return !gl.isContextLost();
            },
            dispose() {
                canvas.removeEventListener('webglcontextlost', onLoss);
                gl.deleteProgram(program);
                gl.getExtension('WEBGL_lose_context')?.loseContext();
                canvas.remove();
            },
        };
    };
})();
