import java.util.*;

class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[] arr = new int[n];
        for(int i=0;i<n;i++){
            arr[i]=sc.nextInt();
            
        }
        
        Arrays.sort(arr);
        for(int i=1;i<n;i++){
            if(arr[i] - arr[i - 1] > 1){
                System.out.print("Missing element: "+(arr[i - 1] + 1));
            }
        }
    }
}