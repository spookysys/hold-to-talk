// Keep a bounded history without polling or retaining window objects.
export class FocusHistory {
    constructor() {
        this.entries = [];
    }

    record(at, token) {
        this.entries.push({at, token});
        if (this.entries.length > 256)
            this.entries.shift();
    }

    capture(at) {
        return this.entries.findLast(entry => entry.at <= at)?.token ?? '';
    }
}
