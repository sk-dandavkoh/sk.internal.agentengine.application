import Carousel from '@/components/ui/Carousel'
import Card from '@/components/ui/Card'

const Vertical = () => {
    return (
        <div className="w-full max-w-xs mx-auto">
            <div className="h-[330px] py-12 relative">
                <Carousel orientation="vertical">
                    <Carousel.Content className="h-[250px]">
                        {Array.from({ length: 5 }).map((_, index) => (
                            <Carousel.Item key={index} className="basis-full">
                                <Card className="p-1">
                                    <div className="flex h-[180px] items-center justify-center p-6">
                                        <span className="text-4xl font-semibold">
                                            {index + 1}
                                        </span>
                                    </div>
                                </Card>
                            </Carousel.Item>
                        ))}
                    </Carousel.Content>
                    <Carousel.Previous className="absolute -top-12 left-1/2 -translate-x-1/2" />
                    <Carousel.Next className="absolute -bottom-12 left-1/2 -translate-x-1/2" />
                </Carousel>
            </div>
        </div>
    )
}

export default Vertical
