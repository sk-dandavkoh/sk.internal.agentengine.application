// Joins class names, skipping falsy entries. No conflict resolution: callers
// pass layout classes (margin, width) only, never restyle a primitive.
export function cx(...classes: (string | false | null | undefined)[]) {
    return classes.filter(Boolean).join(' ')
}
