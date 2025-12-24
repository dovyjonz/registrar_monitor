export default {
    async fetch(request, env, ctx) {
        // This allows the Worker to serve the static assets defined above
        return env.ASSETS.fetch(request);
    },
};