import { useState } from 'react'
import Checkbox from '@/components/ui/Checkbox'

const Controlled = () => {

    const [checked, setChecked] = useState(false)
    const [checkboxList, setCheckboxList] = useState(['Selection A'])

    return (
        <div className="flex flex-col gap-4">
            <Checkbox value={checked} onChange={setChecked}>
                Checkbox
            </Checkbox>
            <Checkbox.Group value={checkboxList} onChange={setCheckboxList}>
                <Checkbox value="Selection A">Selection A </Checkbox>
                <Checkbox value="Selection B">Selection B </Checkbox>
                <Checkbox value="Selection C">Selection C </Checkbox>
            </Checkbox.Group>
        </div>
    )
}

export default Controlled