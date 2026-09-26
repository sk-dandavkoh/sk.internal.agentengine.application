import { useState } from 'react'
import Checkbox from '@/components/ui/Checkbox'

const Indeterminate = () => {
    const [checkboxList, setCheckboxList] = useState(['Selection A'])

    const handleIndeterminate = () => {
        if (checkboxList.length === 0) {
            setCheckboxList(['Selection A', 'Selection B', 'Selection C'])
        } else {
            setCheckboxList([])
        }
    }

    const options: string[] = ['Selection A', 'Selection B', 'Selection C']

    return (
        <div className="flex flex-col gap-4">
            <Checkbox
                indeterminate={checkboxList.length !== options.length}
                checked={checkboxList.length > 0}
                onChange={handleIndeterminate}
            >
                Check all
            </Checkbox>

            <Checkbox.Group value={checkboxList} onChange={setCheckboxList}>
                {options.map((option) => (
                    <Checkbox key={option} value={option}>
                        {option}
                    </Checkbox>
                ))}
            </Checkbox.Group>
        </div>
    )
}

export default Indeterminate
