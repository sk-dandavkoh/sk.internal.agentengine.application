/** A text field's value from FormData ('' when absent or a file). */
export function formText(data: FormData, key: string): string {
    const value = data.get(key)
    return typeof value === 'string' ? value : ''
}
